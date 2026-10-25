"""恢复迁移：把 config 里指向已销毁实例的旧 UUID 配置，按切换链迁移到存活的新实例。

只迁移「能 trace 到当前存活实例」的旧 key；trace 不到或 final 不存活的跳过（不误迁）。
"""
from __future__ import annotations

from app import api_client, config
from app.vultr import VultrAPI

KEYS = ["server_domains", "server_configs", "server_notes", "server_names",
        "server_expiry", "server_cumulative_used"]

# 1. 存活实例
api = VultrAPI(config.get("vultr_api_key", ""))
instances = api.list_instances()
label = config.get("label", "vultr-proxy-node")
managed = [i for i in instances if (i.get("label", "") or "").startswith(label)]
alive_ids = {i["id"] for i in managed}
print(f"存活实例 {len(alive_ids)} 台")

# 2. 切换链 old → new（取最新，后出现覆盖）
flow = api_client.list_switch_flow().get("switches", [])
fwd = {}
for s in flow:
    o, n = s.get("old_id"), s.get("new_id")
    if o and n:
        fwd[o] = n

def trace(uid: str) -> str:
    cur, seen = uid, set()
    while cur in fwd and cur not in seen:
        seen.add(cur)
        cur = fwd[cur]
    return cur

# 3. 找 config 里所有 stale 旧 UUID
all_old = set()
for k in KEYS:
    for uid in (config.get(k, {}) or {}):
        if uid not in alive_ids:
            all_old.add(uid)

print(f"stale 旧 UUID: {len(all_old)} 个")

# 4. 迁移
migrated = 0
for old_id in sorted(all_old):
    final = trace(old_id)
    if final == old_id or final not in alive_ids:
        print(f"  ⏭️  {old_id[:8]} → trace={final[:8]} 跳过（非存活或断链）")
        continue
    # 迁移 6 个 key
    for k in KEYS:
        def _mig(m, _k=k, _o=old_id, _f=final):
            m = dict(m or {})
            if _o in m:
                m[_f] = m.pop(_o)
            return m
        config.mutate(k, _mig)
    print(f"  ✅ {old_id[:8]} → {final[:8]} 已迁移")
    migrated += 1

print(f"\n完成：迁移 {migrated} 个")

# 5. 验证：还剩多少 stale
left = 0
for k in KEYS:
    for uid in (config.get(k, {}) or {}):
        if uid not in alive_ids:
            left += 1
            print(f"  残留 stale: {k} {uid[:8]}")
print(f"迁移后残留 stale key: {left} 个")
