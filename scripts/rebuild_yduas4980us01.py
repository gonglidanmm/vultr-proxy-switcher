"""重建 yduas4980us01：新建 Vultr vhf 实例（sea）→ 域名/证书 → 迁移配置 → 销毁旧实例。

原实例 3919b326（vc2 $5）替换为 vhf-1c-1gb（$6 高频）。
"""
from __future__ import annotations

import time

from app import config, domain, gencreds
from app.vultr import VultrAPI, build_gost_user_data

OLD_ID = "3919b326-7db4-409e-a73e-3b7a99c7ab59"
SUB = "yduas4980us01.yduas.cc"
NOTE = "自己测试"
REGION = "sea"
PLAN = "vhf-1c-1gb"
EXPIRY = "2026-11-10"

KEY = config.get("vultr_api_key", "")
LABEL = config.get("label", "vultr-proxy-node")


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


api = VultrAPI(KEY)

# 1. 生成凭据 + 创建 vhf 实例
log("生成新凭据 + 创建 vhf 实例…")
creds = gencreds.generate_credentials()
ud = build_gost_user_data(creds["proxy_user"], creds["proxy_pass"], int(creds["proxy_port"]), creds)
new = api.create_instance(REGION, PLAN, LABEL, ud)
iid = new["id"]
log(f"新实例 {iid[:8]} ({PLAN})")
gencreds.save_credentials(iid, creds)

# 2. 等 IP
ip = ""
for _ in range(60):
    time.sleep(5)
    d = api.get_instance(iid)
    ip = d.get("main_ip", "0.0.0.0")
    if ip and ip != "0.0.0.0":
        break
if not ip or ip == "0.0.0.0":
    raise SystemExit("❌ 60×5s 仍未拿到 IP")
log(f"新 IP: {ip}")

# 3. 域名 + DNS + 证书
domain.set_server_domain(iid, SUB)
log(f"域名 {SUB} → {iid[:8]}")
time.sleep(40)  # 等 cloud-init 装好 gost/xray
domain.sync_server_dns(iid, ip)
log("DNS 已同步")
time.sleep(5)
pw = creds.get("root_password", "")
signed = False
for _ in range(18):
    try:
        domain.sign_cert(ip, SUB, password=pw)
        signed = True
        break
    except Exception as e:
        log(f"  签证书重试… {e}")
        time.sleep(10)
if not signed:
    log("⚠️ 签证书超时（可稍后手动补签）")

# 4. 迁移旧配置（备注/到期/流量）OLD_ID → 新 UUID
log("迁移旧配置（备注/到期/流量）…")
for k in ("server_notes", "server_expiry", "server_cumulative_used"):
    def _mv(m, _k=k):
        m = dict(m or {})
        if OLD_ID in m:
            m[iid] = m.pop(OLD_ID)
        return m
    config.mutate(k, _mv)
config.mutate("server_notes", lambda m: {**m, iid: NOTE})
config.mutate("server_expiry", lambda m: {**m, iid: EXPIRY})
# 清掉 OLD_ID 的域名残留 + 凭据残留
def _rm_old(m):
    m = dict(m or {})
    m.pop(OLD_ID, None)
    return m
config.mutate("server_domains", _rm_old)
config.mutate("server_configs", _rm_old)

# 5. 销毁旧实例
log("销毁旧实例…")
api.delete_instance(OLD_ID)
log(f"✅ DONE 新 yduas4980us01 = {iid[:8]} IP={ip} 域名={SUB} ({PLAN})")
