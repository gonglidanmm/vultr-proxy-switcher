"""恢复四眼-06（siyan06）的分配记录：重建 node_data + assign + 回填已用流量。"""

from __future__ import annotations

from app import api_client, config, domain, gencreds
from app.share import hysteria2_link, socks5_link, ss_link, vless_reality_link
from app.vultr import VultrAPI, region_label

VID = "76b93523-097c-405e-97b2-499d2147ad8d"
USER_ID = 9  # siyan
QUOTA_GB = 1024.0

# 1. 实例信息（拿真实 date_created / ip / region）
api = VultrAPI(config.get("vultr_api_key", ""))
instances = api.list_instances()
inst = next((i for i in instances if i.get("id") == VID), None)
if not inst:
    raise SystemExit(f"❌ 实例 {VID} 不在 Vultr 存活列表里")

ip = inst.get("main_ip", "")
region = inst.get("region", "")
date_created = (inst.get("date_created", "") or "")[:10]
print(f"实例: {ip} region={region} created={date_created}")

# 2. 域名 + 凭据
host = domain.server_domain(VID) or config.get("domain_name", "") or ip
creds = gencreds.get_credentials(VID)
port = int(creds.get("proxy_port", 1080))
user = creds.get("proxy_user", "")
password = creds.get("proxy_pass", "")
ss_password = creds.get("ss_password", "")
ss_port = int(creds.get("ss_port", 45678))
ruuid = creds.get("reality_uuid", "")
rpub = creds.get("reality_public_key", "")
rsid = creds.get("reality_short_id", "")
rsni = creds.get("reality_sni", "www.cloudflare.com")
rport = int(creds.get("reality_port", 443))
hy_password = creds.get("hysteria_password", "")
hy_port = int(creds.get("hysteria_port", 8443))
hy_mport = creds.get("hysteria_mport", "")

expiry = config.get("server_expiry", {}).get(VID, "")
used_gb = float(config.get("server_cumulative_used", {}).get(VID, 0) or 0)
remaining = max(0.0, QUOTA_GB - used_gb)

def _fmt_gb(g: float) -> str:
    if g >= 1:
        return f"{g:.1f}G"
    return f"{int(g * 1024)}M"

node_data = {
    "host": ip,
    "socks5": {"port": port, "username": user, "password": password},
    "ss": {"port": ss_port, "method": "aes-256-gcm", "password": ss_password},
    "reality": {"port": rport, "uuid": ruuid, "public_key": rpub, "short_id": rsid,
                "sni": rsni, "flow": "xtls-rprx-vision", "fp": "chrome"},
    "hysteria2": {"port": hy_port, "password": hy_password, "mport": hy_mport},
    "links": {
        "socks5": socks5_link(host, port, user, password, "SOCKS5"),
        "ss": ss_link(host, ss_port, "aes-256-gcm", ss_password, "SS"),
        "reality": vless_reality_link(host, rport, ruuid, "xtls-rprx-vision", rsni, "chrome", rpub, rsid, "Reality"),
        "hysteria2": hysteria2_link(host, hy_port, hy_password, hy_mport, "Hysteria2"),
    },
    "name": f"Vultr-{host.split('.')[0]}",
    "location": region_label(region),
    "ip": ip,
    "domain": domain.server_domain(VID) or "",
    "bandwidth": f"{_fmt_gb(used_gb)} / 剩 {_fmt_gb(remaining)}",
    "created_at": date_created,
    "expiry": expiry,
    "quota_gb": QUOTA_GB,
    "client_expiry": f"{expiry} 00:00:00" if expiry else "",
    "_creds": creds,
}

print(f"准备分配: {node_data['name']} 域={host} 位置={node_data['location']} 到期={expiry} 配额={QUOTA_GB}G 已用={used_gb}G")

# 3. 重建分配
r = api_client.assign(USER_ID, "vultr", VID, node_data, expires_at=expiry, quota_gb=QUOTA_GB)
print("assign 返回:", r)

# 4. 回填已用流量
if used_gb > 0:
    try:
        api_client.sync_flow([{"resource_id": VID, "used_gb": used_gb}])
        print(f"已回填已用流量 {used_gb}G")
    except Exception as e:
        print(f"⚠️ 回填流量失败(可后续再同步): {e}")

# 5. 验证
assigns = api_client.list_all_assignments().get("assignments", [])
hit = [a for a in assigns if a.get("resource_id") == VID]
if hit:
    a = hit[0]
    print(f"✅ 恢复成功: 用户={a.get('username')} 节点={a.get('node_data',{}).get('name')} "
          f"IP={a.get('node_data',{}).get('ip')} quota={a.get('quota_gb')}G expiry={a.get('client_expiry')}")
else:
    print("❌ 验证失败：分配列表里仍找不到该节点")
