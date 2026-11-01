#!/usr/bin/env python3
"""给 yong 分配 3 个 relay（24 小时 + 每 IP 10GB）。"""
import json
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, "/Users/konglidan/vultr-proxy-switcher")
from app import config
from app.relay import relay_node_data

cfg = config.get_all()
relays = cfg.get("relays", [])
target_ports = {45709, 45710, 45711}
targets = sorted([r for r in relays if r.get("relay_port") in target_ports],
                 key=lambda r: r.get("relay_port", 0))

if len(targets) != 3:
    print(f"❌ 预期 3 个 relay，找到 {len(targets)} 个：{[r.get('relay_port') for r in targets]}")
    sys.exit(1)

server_ip = "162.35.22.17"
switch_api_key = cfg.get("switch_api_key", "")

tz = timezone(timedelta(hours=8))
expiry = (datetime.now(tz) + timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")

user_id = 10  # yong
quota_gb = 10.0

sql_lines = []
for relay in targets:
    node = relay_node_data(relay, server_ip, switch_api_key)
    node["client_expiry"] = expiry
    node["quota_gb"] = quota_gb
    node_json = json.dumps(node, ensure_ascii=False)
    created_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + "000Z"
    sql = (
        "INSERT INTO assignments (user_id, resource_type, resource_id, node_data, created_at, quota_gb, used_gb, switch_limit, switch_count) "
        f"VALUES ({user_id}, 'relay', '{relay['id']}', '{node_json}', '{created_at}', {quota_gb}, 0, 5, 0);"
    )
    sql_lines.append(sql)

with open("/tmp/assign_yong.sql", "w") as f:
    f.write("\n".join(sql_lines) + "\n")

print(f"分配 {len(targets)} 个 relay 给 yong (user_id={user_id})")
for r in targets:
    print(f"  port={r.get('relay_port')} id={r.get('id')} entry={r.get('entry_domain','')} upstream={r.get('host','')}:{r.get('port','')}")
print(f"client_expiry: {expiry}（24 小时后）")
print(f"quota_gb: {quota_gb} / 每 IP")
print("SQL 已写入 /tmp/assign_yong.sql")
