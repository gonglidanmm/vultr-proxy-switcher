"""验证当前服务器的三协议是否可用（SS + Reality 回环测试，SOCKS5 走本地代理）。

运行：uv run python scripts/verify_protocols.py
"""

from __future__ import annotations

import json
import subprocess

from app import config


def _ssh(host: str, pw: str, cmd: str) -> str:
    r = subprocess.run(
        [
            "sshpass", "-p", pw, "ssh",
            "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=15",
            "-o", "PreferredAuthentications=password", "-o", "PubkeyAuthentication=no",
            f"root@{host}", cmd,
        ],
        capture_output=True, text=True, timeout=120,
    )
    return r.stdout + r.stderr


def main() -> None:
    cache = config.get("active_upstream_cache") or {}
    host = cache.get("host", "")
    if not host:
        print("无当前服务器")
        return
    ss_pass = config.get("ss_password", "")
    ss_port = int(config.get("ss_port", 45678))
    ruuid = config.get("reality_uuid", "")
    rpub = config.get("reality_public_key", "")
    rsid = config.get("reality_short_id", "")
    rsni = config.get("reality_sni", "www.cloudflare.com")
    rport = int(config.get("reality_port", 443))
    root_pw = config.get("root_password", "")

    # Reality 客户端配置（回环连 127.0.0.1:443）
    client_cfg = {
        "log": {"loglevel": "warning"},
        "inbounds": [{"port": 10808, "listen": "127.0.0.1", "protocol": "socks",
                      "settings": {"udp": True}}],
        "outbounds": [{
            "protocol": "vless",
            "settings": {"vnext": [{"address": "127.0.0.1", "port": rport, "users": [
                {"id": ruuid, "encryption": "none", "flow": "xtls-rprx-vision"}]}]},
            "streamSettings": {"network": "tcp", "security": "reality", "realitySettings": {
                "serverName": rsni, "publicKey": rpub, "shortId": rsid, "fingerprint": "chrome"}},
        }],
    }
    cfg_json = json.dumps(client_cfg)

    script = f'''
echo "===== SS 回环测试 (:{ss_port}) ====="
/usr/local/bin/gost -L :10900 -F "ss://aes-256-gcm:{ss_pass}@127.0.0.1:{ss_port}" >/dev/null 2>&1 &
sleep 2
SS_IP=$(curl -s --max-time 10 --socks5-hostname 127.0.0.1:10900 https://api.ipify.org)
pkill -f "gost -L :10900" 2>/dev/null
echo "SS 出口IP: $SS_IP"

echo "===== Reality 回环测试 (:443) ====="
cat > /tmp/xc_test.json <<'XEOF'
{cfg_json}
XEOF
nohup /usr/local/bin/xray run -config /tmp/xc_test.json >/tmp/xc_test.log 2>&1 &
sleep 3
R_IP=$(curl -s --max-time 10 --socks5-hostname 127.0.0.1:10808 https://api.ipify.org)
pkill -f "xray run -config /tmp/xc_test.json" 2>/dev/null
rm -f /tmp/xc_test.json
echo "Reality 出口IP: $R_IP"
'''
    print(f"服务器: {host}")
    print(f"SS 密码:{'已配置' if ss_pass else '❌空'} | Reality UUID:{'已配置' if ruuid else '❌空'} | SNI:{rsni}")
    out = _ssh(host, root_pw, script)
    print(out)


if __name__ == "__main__":
    main()
