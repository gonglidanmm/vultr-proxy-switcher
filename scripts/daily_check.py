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
from app import gencreds  # noqa: E402
from app import e2e  # noqa: E402

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


def real_dns_resolve(domain: str) -> list[str]:
    """用真实 DNS 直连（UDP 53）解析域名 A 记录，绕过本机翻墙软件的 fake-ip 劫持。

    系统 DNS（socket.gethostbyname）在 Shadowrocket/Clash 下会返回 198.18.0.x（fake-ip），
    无法判断真实 DNS 是否同步。这里直接向腾讯/阿里 DNS 发 UDP 查询拿真实 A 记录。
    """
    import struct

    tid = 0x1234
    header = struct.pack(">HHHHHH", tid, 0x0100, 1, 0, 0, 0)
    qname = b"".join(bytes([len(l)]) + l.encode() for l in domain.split(".")) + b"\x00"
    pkt = header + qname + struct.pack(">HH", 1, 1)
    for server in ("119.29.29.29", "223.5.5.5"):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(3)
        data = b""
        try:
            s.sendto(pkt, (server, 53))
            data, _ = s.recvfrom(512)
        except Exception:
            s.close()
            continue
        finally:
            s.close()
        if len(data) < 12:
            continue
        ancount = struct.unpack(">H", data[6:8])[0]
        pos = 12
        while pos < len(data) and data[pos] != 0:
            pos += 1 + data[pos]
        pos += 5
        ips = []
        for _ in range(ancount):
            if pos + 10 > len(data):
                break
            if data[pos] & 0xC0 == 0xC0:
                pos += 2
            else:
                while pos < len(data) and data[pos] != 0:
                    pos += 1 + data[pos]
                pos += 1
            rtype, _rclass, _ttl, rdlen = struct.unpack(">HHIH", data[pos:pos + 10])
            pos += 10
            if rtype == 1 and rdlen == 4:
                ips.append(".".join(str(b) for b in data[pos:pos + 4]))
            pos += rdlen
        if ips:
            return ips
    return []


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
        # DNS（用真实 DNS 直连 119.29.29.29 查询，绕过本机翻墙 fake-ip 劫持——系统 DNS 会返回 198.18.0.x）
        try:
            resolved = real_dns_resolve(dom)
            if resolved and ip not in resolved:
                issues.append(f"DNS 不一致：{dom} 解析到 {resolved[0]}，节点 IP 是 {ip}")
        except Exception:
            pass
        # 卡死（443 Reality 端口）
        if not tcp_open(ip, 443):
            issues.append(f"实例卡死：{dom}（{ip}）443 端口不通")
        # 真实转发检测（域名 + 完整协议，从阿文 Mac 中国网络测：端口通≠能代理，必须走真实流量）
        try:
            creds = gencreds.get_credentials(uid)
        except Exception:
            creds = {}
        test_host = dom or ip
        try:
            lat = e2e.e2e_latencies(
                host=test_host,
                socks_user=creds.get("proxy_user", ""),
                socks_pass=creds.get("proxy_pass", ""),
                socks_port=int(creds.get("proxy_port", 1080)),
                ss_pass=creds.get("ss_password", ""),
                ss_port=int(creds.get("ss_port", 45678)),
                reality_uuid=creds.get("reality_uuid", ""),
                reality_public_key=creds.get("reality_public_key", ""),
                reality_short_id=creds.get("reality_short_id", ""),
                reality_sni=creds.get("reality_sni", "www.cloudflare.com"),
                reality_port=int(creds.get("reality_port", 443)),
                hy_pass=creds.get("hysteria_password", ""),
                hy_port=int(creds.get("hysteria_port", 8443)),
                domain=dom,
            )
        except Exception:
            lat = {}
        ok = [p for p, v in lat.items() if v is not None]
        if lat and not ok:
            issues.append(f"真实转发全部失败：{dom}（{ip}）端口通但各协议转发不通，疑似网络栈卡死，需重切")

    if issues:
        print(f"🚨 巡检发现 {len(issues)} 个异常：")
        for i in issues:
            print(f"  {i}")
    # 无异常时输出为空（watchdog 模式：空 stdout 不打扰，只有出问题才报警）


if __name__ == "__main__":
    main()
