"""把配置里的中转节点应用到服务器（SSH 创建/更新 gost-relay 服务）。

运行：uv run python scripts/apply_relays.py
"""

from __future__ import annotations

import base64
import re
import subprocess
import time

from app import config
from app.relay import build_singbox_relay_config, hysteria_port, reality_port, relay_unit, singbox_relay_unit, socks_port


def _ssh(host: str, password: str, cmd: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            "sshpass", "-p", password, "ssh",
            "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=15",
            f"root@{host}", cmd,
        ],
        capture_output=True, text=True, timeout=120,
    )


def _ensure_cert(host: str, root_pw: str) -> None:
    """确保服务器有正式通配符证书 *.yduas.cc（Hysteria2 的 TLS）。

    缓存复用：证书存在且未过期（60 天）直接跳过，不重复签发——避免 Let's Encrypt
    对同一域名的签发限流（每域名每周 5 次），之前踩过坑。
    """
    # 1. 证书已存在且未过期 → 直接复用
    r = _ssh(host, root_pw,
             "if [ -f /etc/sing-box/cert.pem ] && openssl x509 -checkend 5184000 -noout -in /etc/sing-box/cert.pem 2>/dev/null; "
             "then echo CERT_OK; else echo CERT_NEED; fi")
    if "CERT_OK" in r.stdout:
        return

    token = config.get("cf_token", "")
    account_id = config.get("cf_account_id", "4d72d6d90570f0886a64f7172ba7ef1d")
    if not token:
        print("⚠️ 缺少 cf_token，跳过正式证书签发（Hysteria2 需自签证书 + insecure）")
        return

    script = f"""#!/bin/bash
CERT_HOME=/root/.acme.sh
WILD='*.yduas.cc'
ROOT='yduas.cc'
if [ ! -f "$CERT_HOME/acme.sh" ]; then
  curl -m 120 -s https://get.acme.sh | sh -s email=admin@yduas.cc >/dev/null 2>&1
fi
export CF_Token="{token}"
export CF_Account_ID="{account_id}"
CERT_FILE="$CERT_HOME/*.yduas.cc_ecc/fullchain.cer"
if [ -f "$CERT_FILE" ] && openssl x509 -checkend 5184000 -noout -in "$CERT_FILE" 2>/dev/null; then
  echo "证书已缓存未过期，跳过签发"
else
  "$CERT_HOME/acme.sh" --issue --dns dns_cf -d "$WILD" -d "$ROOT" --keylength ec-256 --server letsencrypt 2>&1 | tail -3
fi
"$CERT_HOME/acme.sh" --install-cert -d "$WILD" --ecc \
  --fullchain-file /etc/sing-box/cert.pem \
  --key-file /etc/sing-box/key.pem \
  --reloadcmd "systemctl restart 'sing-box-relay-*'" 2>&1 | tail -2
"""
    b64 = base64.b64encode(script.encode()).decode()
    r2 = _ssh(host, root_pw, f"echo '{b64}' | base64 -d | bash")
    print(("✅" if r2.returncode == 0 else "❌") + " 正式证书 *.yduas.cc（acme.sh + 缓存复用）")


def main() -> None:
    import sys

    args = sys.argv[1:]
    server_id = args[0] if len(args) > 0 else None
    server_ip = args[1] if len(args) > 1 else None

    # 优先用该服务器的独立 root 密码（Vultr 建机单独生成 / manual 服务器存 manual_servers），fallback 全局
    root_pw = config.get("root_password", "")
    if server_id:
        if server_id.startswith("manual:"):
            mid = server_id[len("manual:"):]
            for m in config.get("manual_servers", []):
                if m.get("id") == mid:
                    root_pw = m.get("password", "") or root_pw
                    break
        else:
            sc = config.get("server_configs", {}).get(server_id, {})
            if sc.get("root_password"):
                root_pw = sc["root_password"]
    if not root_pw:
        print("ERROR: 缺少 root 密码")
        return

    if server_id and server_ip:
        host = server_ip
        relays = [r for r in config.get("relays", []) if r.get("server_id") == server_id]
    else:
        cache = config.get("active_upstream_cache")
        host = (cache or {}).get("host", "")
        relays = config.get("relays", [])
    if not host:
        print("ERROR: 缺少服务器 IP")
        return

    ids = {r["id"] for r in relays}

    # 1. 清理服务器上已不存在的中转服务（gost + sing-box 都要清理，防孤儿进程堆积）
    r = _ssh(host, root_pw, "ls /etc/systemd/system/ 2>/dev/null | grep -E '^(gost-relay|sing-box-relay)-' || true")
    for line in r.stdout.splitlines():
        name = line.strip().replace(".service", "")
        for prefix in ("gost-relay-", "sing-box-relay-"):
            if name.startswith(prefix):
                rid = name[len(prefix):]
                if rid not in ids:
                    _ssh(host, root_pw,
                         f"systemctl disable --now {name} 2>/dev/null; "
                         f"rm -f /etc/systemd/system/{name}.service /etc/sing-box/relay-{rid}.json 2>/dev/null")
                    print(f"  已删除旧中转: {name}")

    # 1.5 停掉旧的单进程 sing-box（迁移到 per-relay sing-box-relay-*，幂等）
    _ssh(host, root_pw,
         "systemctl disable --now sing-box 2>/dev/null; "
         "rm -f /etc/systemd/system/sing-box.service /etc/sing-box/config.json 2>/dev/null")

    # 1.6 确保正式通配符证书 *.yduas.cc（Hysteria2 的 TLS，缓存复用避免限流）
    if relays:
        _ensure_cert(host, root_pw)

    # 2. 应用每个中转（gost + sing-box 双独立进程，并行 SSH）
    from concurrent.futures import ThreadPoolExecutor

    def _deploy_one(rel: dict):
        rid = rel["id"]
        rp = int(rel.get("relay_port", 0))
        results = []

        # gost（SS + SOCKS5 入站）
        unit = relay_unit(
            rel.get("name", "relay"), rel.get("relay_password", ""), rp,
            rel.get("host", ""), int(rel.get("port", 1080)),
            rel.get("user", ""), rel.get("password", ""),
        )
        ub = base64.b64encode(unit.encode()).decode()
        sp = socks_port(rp)
        r0 = _ssh(host, root_pw,
                  f"cat /etc/systemd/system/gost-relay-{rid}.service 2>/dev/null || echo __MISSING__")
        if r0.returncode == 0 and r0.stdout.strip() == unit.strip():
            results.append(("gost", "skip"))
        else:
            cmd = (
                f"ufw allow {rp}/tcp 2>/dev/null; ufw allow {rp}/udp 2>/dev/null; "
                f"ufw allow {sp}/tcp 2>/dev/null; ufw allow {sp}/udp 2>/dev/null; "
                f"echo '{ub}' | base64 -d > /etc/systemd/system/gost-relay-{rid}.service; "
                f"systemctl daemon-reload; systemctl enable gost-relay-{rid}; "
                f"systemctl restart gost-relay-{rid}"
            )
            r2 = _ssh(host, root_pw, cmd)
            # 偶发 SSH 失败重试（最多 3 次），与 sing-box 一致，避免漏部署 gost（踩过坑：1 个 relay 只有 sing-box 没 gost）
            for _attempt in range(3):
                if r2.returncode == 0:
                    break
                time.sleep(2 * (_attempt + 1))
                r2 = _ssh(host, root_pw, cmd)
            results.append(("gost", "deploy" if r2.returncode == 0 else "fail"))

        # sing-box（Reality + Hysteria2 入站，每 relay 一个独立进程）
        sb_cfg = build_singbox_relay_config(rel)
        sb_cfg_b64 = base64.b64encode(sb_cfg.encode()).decode()
        sb_unit = singbox_relay_unit(rid)
        sb_unit_b64 = base64.b64encode(sb_unit.encode()).decode()
        r1 = _ssh(host, root_pw,
                  f"cat /etc/sing-box/relay-{rid}.json 2>/dev/null || echo __MISSING__")
        if r1.returncode == 0 and r1.stdout.strip() == sb_cfg.strip():
            results.append(("singbox", "skip"))
        else:
            ufw_sb = "; ".join(
                f"ufw allow {p}/tcp 2>/dev/null; ufw allow {p}/udp 2>/dev/null"
                for p in (reality_port(rp), hysteria_port(rp))
            )
            cmd2 = (
                f"mkdir -p /etc/sing-box; "
                f"echo '{sb_cfg_b64}' | base64 -d > /etc/sing-box/relay-{rid}.json; "
                f"echo '{sb_unit_b64}' | base64 -d > /etc/systemd/system/sing-box-relay-{rid}.service; "
                f"{ufw_sb}; "
                f"systemctl daemon-reload; systemctl enable sing-box-relay-{rid}; "
                f"systemctl restart sing-box-relay-{rid}"
            )
            # 偶发 SSH 失败重试（最多 3 次），避免漏部署 sing-box（之前踩过坑：1 个 relay 只部署了 gost 没 sing-box）
            r3 = _ssh(host, root_pw, cmd2)
            for _attempt in range(3):
                if r3.returncode == 0:
                    break
                time.sleep(2 * (_attempt + 1))
                r3 = _ssh(host, root_pw, cmd2)
            results.append(("singbox", "deploy" if r3.returncode == 0 else "fail"))

        return rel, results

    with ThreadPoolExecutor(max_workers=6) as ex:
        for rel, results in ex.map(_deploy_one, relays):
            rp = int(rel.get("relay_port", 0))
            sp = socks_port(rp)
            acts = dict(results)
            ok = all(a != "fail" for _, a in results)
            tag = "✅" if ok else "❌"
            print(f"{tag} 中转「{rel.get('name','')}」 "
                  f"SS:{rp}/SOCKS5:{sp}/Reality:{reality_port(rp)}/Hysteria2:{hysteria_port(rp)} "
                  f"-> {rel.get('host')}:{rel.get('port')}"
                  + (f"（gost:{acts.get('gost','?')} singbox:{acts.get('singbox','?')}）"))

    print("DONE")


if __name__ == "__main__":
    main()
