"""只读分析：找出哪些存活实例丢了配置（名称/备注/域名/凭据/到期），并给出恢复映射。

不写任何数据。输出报告。
"""
from __future__ import annotations

import json
from collections import defaultdict

from app import api_client, config
from app.vultr import VultrAPI

# 1. 当前 config
cur = config.get_all()
KEYS = ["server_notes", "server_names", "server_domains", "server_configs", "server_expiry", "server_cumulative_used"]

# 2. Vultr 存活实例
api = VultrAPI(config.get("vultr_api_key", ""))
instances = api.list_instances()
label = config.get("label", "vultr-proxy-node")
managed = [i for i in instances if (i.get("label", "") or "").startswith(label)]
alive_ids = {i["id"] for i in managed}
print(f"=== Vultr 存活实例（label 匹配）: {len(alive_ids)} 台 ===")
for i in managed:
    print(f"  {i['id'][:8]}  {i.get('main_ip',''):16s} region={i.get('region','')}")

# 3. 切换记录 → 完整链
flow = api_client.list_switch_flow().get("switches", [])
fwd = {}
for s in flow:
    o, n = s.get("old_id"), s.get("new_id")
    if o and n:
        fwd[o] = n  # 注意：同一 old 可能切多次，取最新（后出现的覆盖）
print(f"\n=== 切换记录 {len(flow)} 条，链映射 {len(fwd)} 条 ===")

# 4. 反向链：new → 其所有历史 old（可能多个）
rev = defaultdict(list)
for s in flow:
    o, n = s.get("old_id"), s.get("new_id")
    if o and n:
        rev[n].append(o)

# 5. 交叉分析
print("\n=== 每台存活实例的配置缺失情况 ===")
missing_report = []
for aid in sorted(alive_ids):
    row = {}
    for k in KEYS:
        v = cur.get(k, {}) or {}
        row[k] = aid in v
    has_all = all(row.values())
    missing = [k for k in KEYS if not row[k]]
    status = "✅ 完整" if has_all else f"❌ 缺 {missing}"
    print(f"  {aid[:8]}: {status}")
    if not has_all:
        missing_report.append((aid, missing))

# 6. config 里指向已销毁实例的 stale key（不该再存在的旧 UUID）
print("\n=== config 里指向「非存活」实例的旧 UUID（stale）===")
for k in KEYS:
    v = cur.get(k, {}) or {}
    stale = [uid for uid in v if uid not in alive_ids]
    if stale:
        print(f"  {k}: {len(stale)} 个 stale key")
        for uid in stale:
            print(f"    {uid[:8]} = {str(v[uid])[:60]}")

# 7. 反向追溯：每个存活实例缺失的配置，能从哪个旧 UUID 找到
print("\n=== 恢复映射建议（存活实例 ← 历史旧 UUID 的配置）===")
for aid, missing in missing_report:
    # 沿着 rev 链向上找有配置的旧 UUID
    ancestors = []
    stack = [aid]
    seen = set()
    while stack:
        u = stack.pop()
        for o in rev.get(u, []):
            if o not in seen:
                seen.add(o)
                ancestors.append(o)
                stack.append(o)
    # 在这些祖先里，哪些还有配置
    for k in missing:
        v = cur.get(k, {}) or {}
        found = [o for o in ancestors if o in v]
        if found:
            print(f"  {aid[:8]} 缺 {k}: 可从旧 UUID {[o[:8] for o in found]} 恢复")
        else:
            print(f"  {aid[:8]} 缺 {k}: ❌ 当前 config 无任何祖先有该配置（需查备份）")
