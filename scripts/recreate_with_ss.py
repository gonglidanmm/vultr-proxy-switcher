"""重建服务器：gost 同时跑 SOCKS5 + Shadowsocks，并设固定 root 密码。

运行：uv run python scripts/recreate_with_ss.py
"""

from __future__ import annotations

import base64
import time

from app import config
from app.check import exit_ip_and_latency
from app.cloudflare import sync_domain
from app.vultr import VultrAPI

SS_PASSWORD = "01c03e02412be0c1fff75dfea7f4f8fe"
SS_PORT = 45678
ROOT_PASSWORD = "1f8429666df389c6844545ad29608674"

KEY = config.get("vultr_api_key", "")
LABEL = config.get("label", "vultr-proxy-node")
USER = config.get("proxy_user", "")
PASSWORD = config.get("proxy_pass", "")
PORT = int(config.get("proxy_port", 1080))
TARGET = "atl"


def cloud_init() -> str:
    script = f"""#!/bin/bash
set -e
apt-get update -y >/dev/null 2>&1 || true
echo 'root:{ROOT_PASSWORD}' | chpasswd
ufw allow {PORT}/tcp 2>/dev/null; ufw allow {PORT}/udp 2>/dev/null
ufw allow {SS_PORT}/tcp 2>/dev/null; ufw allow {SS_PORT}/udp 2>/dev/null
ufw allow 22/tcp 2>/dev/null
ufw --force enable 2>/dev/null || true
if [ ! -x /usr/local/bin/gost ]; then
  wget -q -O /usr/local/bin/gost.gz https://github.com/ginuerzh/gost/releases/download/v2.11.5/gost-linux-amd64-2.11.5.gz
  gunzip -f /usr/local/bin/gost.gz
  chmod +x /usr/local/bin/gost
fi
cat > /etc/systemd/system/gost.service <<EOF
[Unit]
Description=gost proxy (socks5 + shadowsocks)
After=network.target
[Service]
ExecStart=/usr/local/bin/gost -L socks5://{USER}:{PASSWORD}@:{PORT} -L ss://aes-256-gcm:{SS_PASSWORD}@:{SS_PORT}
Restart=always
RestartSec=3
LimitNOFILE=65535
[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable gost
systemctl restart gost
"""
    return base64.b64encode(script.encode()).decode()


api = VultrAPI(KEY)

# 1. 销毁旧机
insts = [i for i in api.list_instances() if i.get("label") == LABEL]
for i in insts:
    api.delete_instance(i["id"])
    print(f"已销毁 {i.get('region')} {i.get('main_ip')}")

# 2. 建新机
ud = cloud_init()
new = None
for plan in config.get("plans", ["vc2-1c-1gb", "vhf-1c-1gb", "vhp-1c-2gb"]):
    try:
        new = api.create_instance(TARGET, plan, LABEL, ud)
        print(f"创建成功：{TARGET} / {plan}")
        break
    except Exception as e:
        print(f"  {TARGET}/{plan} 失败：{e}")
if not new:
    raise SystemExit("ERROR: 创建失败（缺货）")

iid = new["id"]

# 3. 等 IP
d = {}
for _ in range(60):
    time.sleep(5)
    d = api.get_instance(iid)
    ip = d.get("main_ip", "0.0.0.0")
    if ip and ip != "0.0.0.0":
        break
new_ip = d.get("main_ip", "")
print(f"新 IP：{new_ip}")

# 4. 等 gost(SOCKS5) 就绪
for _ in range(24):
    time.sleep(5)
    if exit_ip_and_latency(new_ip, PORT, USER, PASSWORD):
        print("gost SOCKS5 就绪")
        break

# 5. 存配置
config.update(
    active_upstream_id=iid,
    active_upstream_cache={"host": new_ip, "port": PORT, "user": USER, "password": PASSWORD, "name": LABEL},
    ss_password=SS_PASSWORD,
    ss_port=SS_PORT,
    root_password=ROOT_PASSWORD,
)

# 6. 同步域名
domain = config.get("domain_name", "")
token = config.get("cf_token", "")
zid = config.get("zone_id", "")
if domain and token:
    action, _rid = sync_domain(token, zid, domain, new_ip)
    print(f"域名：{domain} -> {new_ip}（{action}）")

print(f"DONE 新IP={new_ip} SS端口={SS_PORT} SS密码={SS_PASSWORD} root密码={ROOT_PASSWORD}")
