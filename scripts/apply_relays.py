"""把配置里的中转节点应用到服务器（SSH 创建/更新 gost-relay 服务）。

运行：uv run python scripts/apply_relays.py
"""

from __future__ import annotations

import base64
import subprocess

from app import config
from app.relay import relay_unit


def _ssh(host: str, password: str, cmd: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            "sshpass", "-p", password, "ssh",
            "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=15",
            f"root@{host}", cmd,
        ],
        capture_output=True, text=True, timeout=120,
    )


def main() -> None:
    import sys

    args = sys.argv[1:]
    server_id = args[0] if len(args) > 0 else None
    server_ip = args[1] if len(args) > 1 else None

    root_pw = config.get("root_password", "")
    if not root_pw:
        print("ERROR: 缺少 root 密码（需先用 App 建过服务器）")
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

    # 1. 清理服务器上已不存在的中转服务
    r = _ssh(host, root_pw, "ls /etc/systemd/system/ 2>/dev/null | grep '^gost-relay-' || true")
    for line in r.stdout.splitlines():
        name = line.strip().replace(".service", "")
        if name.startswith("gost-relay-"):
            rid = name[len("gost-relay-"):]
            if rid not in ids:
                _ssh(host, root_pw, f"systemctl disable --now {name} 2>/dev/null; rm -f /etc/systemd/system/{name}.service")
                print(f"  已删除旧中转: {name}")

    # 2. 应用每个中转
    for rel in relays:
        rid = rel["id"]
        rp = int(rel.get("relay_port", 0))
        unit = relay_unit(
            rel.get("name", "relay"), rel.get("relay_password", ""), rp,
            rel.get("host", ""), int(rel.get("port", 1080)),
            rel.get("user", ""), rel.get("password", ""),
        )
        ub = base64.b64encode(unit.encode()).decode()
        cmd = (
            f"ufw allow {rp}/tcp 2>/dev/null; ufw allow {rp}/udp 2>/dev/null; "
            f"echo '{ub}' | base64 -d > /etc/systemd/system/gost-relay-{rid}.service; "
            f"systemctl daemon-reload; systemctl enable gost-relay-{rid}; "
            f"systemctl restart gost-relay-{rid}"
        )
        r2 = _ssh(host, root_pw, cmd)
        ok = r2.returncode == 0
        print(f"{'✅' if ok else '❌'} 中转「{rel.get('name','')}」 SS端口 {rp} -> {rel.get('host')}:{rel.get('port')}")

    print("DONE")


if __name__ == "__main__":
    main()
