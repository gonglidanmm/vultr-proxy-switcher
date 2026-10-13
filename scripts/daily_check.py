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
import subprocess

sys.path.insert(0, "/Users/konglidan/vultr-proxy-switcher")
from app import config, vultr  # noqa: E402
from app import gencreds  # noqa: E402

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


def proxy_forward_ok(ip: str, creds: dict, timeout: float = 8.0) -> bool:
    """通过节点 SOCKS5 代理实际访问网站，验证「转发」是否正常（不只测端口）。

    端口通 ≠ 转发通：Vultr 网络栈卡死时 gost 仍监听端口但出站转发断，端口检测测不出，
    必须走真实流量才能发现。返回 None 表示无法检测（无凭据）。
    """
    user = (creds or {}).get("proxy_user", "")
    pwd = (creds or {}).get("proxy_pass", "")
    port = int((creds or {}).get("proxy_port", 1080))
    if not user:
        return None
    try:
        r = subprocess.run(
            ["curl", "-s", "-x", f"socks5h://{user}:{pwd}@{ip}:{port}",
             "--connect-timeout", str(int(timeout)), "-o", "/dev/null",
             "-w", "%{http_code}", "https://www.google.com"],
            capture_output=True, text=True, timeout=timeout + 5,
        )
        return r.stdout.strip() == "200"
    except Exception:
        return False


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
        # 转发检测（端口通但转发断 = 网络栈卡死，端口检测测不出，必须走真实流量）
        try:
            creds = gencreds.get_credentials(uid)
        except Exception:
            creds = {}
        fwd = proxy_forward_ok(ip, creds)
        if fwd is False:
            issues.append(f"转发失败：{dom}（{ip}）SOCKS5 端口通但出站转发断，疑似网络栈卡死，需重切")

    if issues:
        print(f"🚨 巡检发现 {len(issues)} 个异常：")
        for i in issues:
            print(f"  {i}")
    # 无异常时输出为空（watchdog 模式：空 stdout 不打扰，只有出问题才报警）


if __name__ == "__main__":
    main()
