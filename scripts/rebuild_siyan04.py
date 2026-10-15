"""重建四眼-04（siyan04）：新建 Vultr 实例（lax）→ 域名/证书 → 恢复备注/到期/流量 → 分配。

原 siyan04 实例 8531e9d0 已销毁（404），其旧配置挂在 3df61c5a（四眼-04 前身）上。
"""
from __future__ import annotations

import time

from app import api_client, config, domain, gencreds
from app.vultr import VultrAPI, build_gost_user_data

OLD_ID = "3df61c5a-f95e-45aa-8d34-5032af7ea668"   # 四眼-04 前身（配置还在这个 UUID 下）
DEAD_ID = "8531e9d0-32b8-4f7a-a6bb-22e591572455"   # 已销毁的 siyan04（分配还指向它）
SUB = "siyan04.yduas.cc"
NOTE = "四眼-04"
REGION = "lax"
USER_ID = 9  # siyan
QUOTA_GB = 1024.0
EXPIRY = "2026-11-10"

KEY = config.get("vultr_api_key", "")
LABEL = config.get("label", "vultr-proxy-node")

def log(msg): print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

api = VultrAPI(KEY)

# 1. 生成新凭据 + 创建实例
log("生成新凭据 + 创建 lax 实例…")
creds = gencreds.generate_credentials()
ud = build_gost_user_data(creds["proxy_user"], creds["proxy_pass"], int(creds["proxy_port"]), creds)
new = None
for plan in config.get("plans", ["vc2-1c-1gb", "vhp-1c-1gb-amd"]):
    try:
        new = api.create_instance(REGION, plan, LABEL, ud)
        log(f"创建成功 {REGION}/{plan}")
        break
    except Exception as e:
        log(f"  {REGION}/{plan} 失败：{e}")
if not new:
    raise SystemExit("❌ 创建失败（lax 缺货）")
iid = new["id"]
log(f"新实例 {iid[:8]}")
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
    raise SystemExit(f"❌ 60×5s 仍未拿到 IP")
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

# 4. 迁移旧配置（备注/到期/累计流量）3df61c5a → 新 UUID
log("迁移旧配置（备注/到期/流量）…")
for k in ("server_notes", "server_expiry", "server_cumulative_used"):
    def _mv(m, _k=k):
        m = dict(m or {})
        if OLD_ID in m:
            m[iid] = m.pop(OLD_ID)
        return m
    config.mutate(k, _mv)
# 确认备注/到期正确
config.mutate("server_notes", lambda m: {**m, iid: NOTE})
config.mutate("server_expiry", lambda m: {**m, iid: EXPIRY})
# 清掉 OLD_ID 的域名残留
def _rm_old_dom(m):
    m = dict(m or {})
    m.pop(OLD_ID, None)
    return m
config.mutate("server_domains", _rm_old_dom)
log(f"备注={NOTE} 到期={EXPIRY}")

# 5. 解绑已销毁的旧分配 8531e9d0
try:
    api_client.unassign(USER_ID, "vultr", DEAD_ID)
    log("已解绑旧分配 8531e9d0")
except Exception as e:
    log(f"解绑旧分配跳过：{e}")

# 6. 分配新实例给 siyan
used_gb = float(config.get("server_cumulative_used", {}).get(iid, 0) or 0)
node_data = {
    "host": ip,
    "socks5": {"port": int(creds["proxy_port"]), "username": creds["proxy_user"], "password": creds["proxy_pass"]},
    "ss": {"port": int(creds["ss_port"]), "method": "aes-256-gcm", "password": creds["ss_password"]},
    "reality": {"port": int(creds["reality_port"]), "uuid": creds["reality_uuid"], "public_key": creds["reality_public_key"],
                "short_id": creds["reality_short_id"], "sni": creds["reality_sni"], "flow": "xtls-rprx-vision", "fp": "chrome"},
    "hysteria2": {"port": int(creds["hysteria_port"]), "password": creds["hysteria_password"], "mport": creds["hysteria_mport"]},
    "name": "Vultr-siyan04",
    "location": "美国·洛杉矶",
    "ip": ip,
    "domain": SUB,
    "expiry": EXPIRY,
    "quota_gb": QUOTA_GB,
    "client_expiry": f"{EXPIRY} 00:00:00",
    "_creds": creds,
}
r = api_client.assign(USER_ID, "vultr", iid, node_data, expires_at=EXPIRY, quota_gb=QUOTA_GB)
log(f"分配结果: {r}")
if used_gb > 0:
    api_client.sync_flow([{"resource_id": iid, "used_gb": used_gb}])
    log(f"回填流量 {used_gb}G")

log(f"✅ DONE 新 siyan04 = {iid[:8]} IP={ip} 域名={SUB}")
