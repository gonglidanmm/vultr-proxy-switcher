#!/usr/bin/env python3
"""vultr-proxy-switcher 每日巡检。

检查三类异常（商业系统主动报警，不等客户投诉）：
1. 配置漂移：config 里指向已销毁实例的旧 UUID（切换 IP 后域名/凭据没迁移）
2. DNS 不一致：域名解析结果 ≠ 节点当前 IP（切换 IP 后 DNS 没同步）
3. 实例卡死：节点 443 端口不通（网络栈卡死 / xray 没起来）

输出异常清单；无异常输出「✅ 巡检通过」。
"""
import sys
import socket

sys.path.insert(0, "/Users/konglidan/vultr-proxy-switcher")
from app import config, vultr  # noqa: E402

issues: list[str] = []


def tcp_open(ip: str, port: int, timeout: float = 5.0) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect((ip, port))
        return True
    except Exception:
        return False
    finally:
        s.close()


def main() -> None:
    key = config.get("vultr_api_key", "")
    if not key:
        print("⚠️ 未配置 Vultr API key，跳过巡检")
        return

    try:
        api = vultr.VultrAPI(key)
        instances = api.list_instances()
    except Exception as e:
        print(f"❌ 无法连接 Vultr API：{e}")
        return

    alive_ids = {i["id"] for i in instances}
    by_id = {i["id"]: i for i in instances}
    domains = dict(config.get("server_domains", {}) or {})

    # 1. 配置漂移：config 里有但实例已销毁的旧 UUID
    for uid, dom in domains.items():
        if uid not in alive_ids:
            issues.append(f"配置漂移：{dom} 的实例 {uid[:8]} 已销毁，域名/凭据/备注未迁移")

    # 2. DNS 不一致 + 3. 实例卡死（只查当前存活实例）
    for uid, dom in domains.items():
        inst = by_id.get(uid)
        if not inst:
            continue
        ip = inst.get("main_ip", "")
        if not ip:
            continue
        # DNS
        try:
            resolved = socket.gethostbyname(dom)
            if resolved != ip:
                issues.append(f"DNS 不一致：{dom} 解析到 {resolved}，节点 IP 是 {ip}")
        except Exception:
            issues.append(f"DNS 解析失败：{dom}")
        # 卡死（443 Reality 端口）
        if not tcp_open(ip, 443):
            issues.append(f"实例卡死：{dom}（{ip}）443 端口不通")

    if issues:
        print(f"🚨 巡检发现 {len(issues)} 个异常：")
        for i in issues:
            print(f"  {i}")
    # 无异常时输出为空（watchdog 模式：空 stdout 不打扰，只有出问题才报警）


if __name__ == "__main__":
    main()
