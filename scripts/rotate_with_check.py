"""切换到能直连的 IP：销毁重建，检测国内直连连通性，被墙就自动重切。

运行：uv run python scripts/rotate_with_check.py
"""

from __future__ import annotations

import socket
import time

from app import config
from app.cloudflare import sync_domain
from app.vultr import COUNTRY_REGIONS, REGION_COUNTRY, VultrAPI, build_gost_user_data


def tcp_ok(ip: str, port: int, timeout: float = 4) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect((ip, port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def main() -> None:
    key = config.get("vultr_api_key", "")
    label = config.get("label", "vultr-proxy-node")
    user = config.get("proxy_user", "")
    password = config.get("proxy_pass", "")
    port = int(config.get("proxy_port", 1080))
    plans = config.get("plans", ["vc2-1c-1gb", "vhf-1c-1gb", "vhp-1c-2gb"])
    ud = build_gost_user_data(user, password, port)
    api = VultrAPI(key)

    # 找当前实例
    current_region = "atl"
    for i in api.list_instances():
        if i.get("label") == label:
            current_region = i.get("region", "atl")
            print(f"销毁当前实例 {i.get('main_ip')} ({i.get('id')}) ...")
            api.delete_instance(i["id"])
            time.sleep(3)
            break

    country = REGION_COUNTRY.get(current_region, "us")
    regions = COUNTRY_REGIONS.get(country, [current_region])
    print(f"目标国家区域：{' → '.join(regions)}")

    for attempt in range(1, 9):
        for reg in regions:
            for plan in plans:
                try:
                    new = api.create_instance(reg, plan, label, ud)
                except Exception as e:
                    print(f"  {reg}/{plan} 创建失败：{e}")
                    continue
                iid = new.get("id", "")
                print(f"[第{attempt}次] 已建 {reg}/{plan} (id={iid})，等待分配 IP ...")
                ip = ""
                for _ in range(30):
                    time.sleep(4)
                    d = api.get_instance(iid)
                    ip = d.get("main_ip", "")
                    if ip and ip != "0.0.0.0":
                        break
                if not ip or ip == "0.0.0.0":
                    print("  未拿到 IP，销毁重试")
                    api.delete_instance(iid)
                    continue

                # 等实例启动，测 22（国内直连，判断是否被墙）
                print(f"  新 IP {ip}，等 SSH 就绪并检测是否被墙 ...")
                ssh_ok = False
                for _ in range(4):
                    if tcp_ok(ip, 22):
                        ssh_ok = True
                        break
                    time.sleep(5)
                if not ssh_ok:
                    print("  22端口暂不通，等待实例启动 60 秒再测...")
                    time.sleep(60)
                    ssh_ok = tcp_ok(ip, 22)
                if not ssh_ok:
                    print(f"  ❌ {ip} 22端口不通（大概率被墙），销毁重切")
                    api.delete_instance(iid)
                    continue

                print(f"  ✅ {ip} 22端口通，IP 可用，等 gost 装好 ...")
                ready = False
                for _ in range(30):
                    time.sleep(6)
                    if tcp_ok(ip, port):
                        ready = True
                        break
                if not ready:
                    print(f"  ⚠️ gost 未就绪，销毁重切")
                    api.delete_instance(iid)
                    continue

                # 成功
                print(f"  🎉 可用 IP：{ip}（gost 端口 {port} 通）")
                config.set_value("active_upstream_id", iid)
                config.set_value("active_upstream_cache", {
                    "id": iid, "host": ip, "port": port,
                    "user": user, "password": password, "name": label,
                    "region": reg, "type": "managed",
                })
                # 同步域名
                try:
                    token = config.get("cf_token", "")
                    zone = config.get("zone_id", "")
                    domain = config.get("domain_name", "")
                    if token and zone and domain:
                        action, rid = sync_domain(token, zone, domain, ip)
                        print(f"  域名 {domain} → {ip}（{action} {rid}）")
                except Exception as e:
                    print(f"  域名同步失败（不影响代理）：{e}")
                print(f"DONE 新IP={ip}")
                return

    print("❌ 多次尝试均未拿到可直连的 IP，建议换区域或稍后再试")


if __name__ == "__main__":
    main()
