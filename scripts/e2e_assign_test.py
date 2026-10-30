#!/usr/bin/env python3
"""e2e 测试：分配 relay 45735 给 test_switch（5分钟时长），写 D1，验证 cron 自动回收。"""
import json
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, "/Users/konglidan/vultr-proxy-switcher")
from app import config
from app.relay import relay_node_data

cfg = config.get_all()
relays = cfg.get("relays", [])
relay = next((r for r in relays if r.get("relay_port") == 45735), None)
if not relay:
    print("❌ 找不到 relay_port=45735"); sys.exit(1)

server_ip = "162.35.22.17"
switch_api_key = cfg.get("switch_api_key", "")
node = relay_node_data(relay, server_ip, switch_api_key)

tz = timezone(timedelta(hours=8))
expiry = (datetime.now(tz) + timedelta(seconds=60)).strftime("%Y-%m-%d %H:%M:%S")
node["client_expiry"] = expiry

user_id = 8  # test_switch
quota_gb = 100.0
node_json = json.dumps(node, ensure_ascii=False)
created_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + "000Z"

sql = (
    "INSERT INTO assignments (user_id, resource_type, resource_id, node_data, created_at, quota_gb, used_gb, switch_limit, switch_count) "
    f"VALUES ({user_id}, 'relay', '{relay['id']}', '{node_json}', '{created_at}', {quota_gb}, 0, 5, 0);"
)

with open("/tmp/assign_test.sql", "w") as f:
    f.write(sql + "\n")

print(f"relay_id: {relay['id']}")
print(f"relay_port: {relay['relay_port']} (SS) / {relay['relay_port']+8000} (Reality)")
print(f"user: test_switch (id={user_id})")
print(f"client_expiry: {expiry}  (5分钟后到期)")
print(f"quota_gb: {quota_gb}")
print(f"SQL 已写入 /tmp/assign_test.sql ({len(sql)} 字符)")
