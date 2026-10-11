"""紧急修复：销毁日本机，重建美国机（亚特兰大），恢复美国 IP。

运行：uv run python scripts/fix_to_us.py
"""

from __future__ import annotations

import time

from app import config
from app.check import exit_ip_and_latency
from app.cloudflare import sync_domain
from app.vultr import VultrAPI, build_gost_user_data

KEY = config.get("vultr_api_key", "")
LABEL = config.get("label", "vultr-proxy-node")
USER = config.get("proxy_user", "")
PASSWORD = config.get("proxy_pass", "")
PORT = int(config.get("proxy_port", 1080))
TARGET_REGION = "atl"  # 亚特兰大（美国）

api = VultrAPI(KEY)
ud = build_gost_user_data(USER, PASSWORD, PORT)

# 1. 销毁所有 vultr-proxy-node 实例
insts = [i for i in api.list_instances() if i.get("label") == LABEL]
print(f"销毁 {len(insts)} 台旧机...")
for i in insts:
    api.delete_instance(i["id"])
    print(f"  已销毁 {i.get('region')} {i.get('main_ip')}")

# 2. 建美国机（阶梯套餐）
print(f"创建美国机 {TARGET_REGION} ...")
new = None
for plan in config.get("plans", ["vc2-1c-1gb", "vhf-1c-1gb", "vhp-1c-2gb"]):
    try:
        new = api.create_instance(TARGET_REGION, plan, LABEL, ud)
        print(f"  创建成功：{TARGET_REGION} / {plan}")
        break
    except Exception as e:
        print(f"  {TARGET_REGION}/{plan} 失败：{e}")
if not new:
    raise SystemExit("ERROR: 美国机创建失败（缺货）")

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

# 4. 等 gost 就绪
for _ in range(24):
    time.sleep(5)
    if exit_ip_and_latency(new_ip, PORT, USER, PASSWORD):
        print("gost 就绪")
        break

# 5. 更新配置
config.update(
    active_upstream_id=iid,
    active_upstream_cache={
        "host": new_ip,
        "port": PORT,
        "user": USER,
        "password": PASSWORD,
        "name": LABEL,
    },
)

# 6. 同步域名
domain = config.get("domain_name", "")
token = config.get("cf_token", "")
zid = config.get("zone_id", "")
if domain and token:
    action, _rid = sync_domain(token, zid, domain, new_ip)
    print(f"域名同步：{domain} -> {new_ip}（{action}）")

print(f"DONE：新美国 IP {new_ip}")
