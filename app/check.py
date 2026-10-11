"""出口 IP / 延迟检测（通过 SOCKS5 代理发请求）。"""

from __future__ import annotations

import socket
import time

import requests


def tcp_ok(host: str, port: int, timeout: float = 7) -> bool:
    """TCP 连接检测（本机直连，用于判断新 IP 是否被墙）。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect((host, port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def tcp_latency(host: str, port: int, timeout: float = 8) -> int | None:
    """TCP 连接延迟(ms)，连接失败返回 None。瞬时失败重试一次。"""
    for attempt in range(2):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        try:
            t0 = time.time()
            s.connect((host, port))
            return int((time.time() - t0) * 1000)
        except OSError:
            if attempt == 0:
                time.sleep(0.5)
        finally:
            s.close()
    return None


def udp_open(host: str, port: int, timeout: float = 5) -> bool:
    """UDP 端口是否开放：发探测包，收到 ICMP unreachable=关闭，超时=开放。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(timeout)
    try:
        s.sendto(b"\x00" * 32, (host, port))
        try:
            s.recvfrom(1024)
            return False  # 收到 unreachable = 端口关闭
        except socket.timeout:
            return True  # 超时 = 端口开放（服务端忽略无效包）
    except OSError:
        return False
    finally:
        s.close()


def vpn_tunnel_active() -> bool:
    """检测是否有代理 VPN 隧道（如 Shadowrocket 的 MacPacketTunnel）在劫持直连流量。

    隧道开着时，TCP connect 会被本地 accept，导致直连延迟测到 0ms（假数据）。
    """
    import subprocess

    try:
        r = subprocess.run(
            ["pgrep", "-f", "MacPacketTunnel"],
            capture_output=True,
            timeout=5,
        )
        return r.returncode == 0
    except Exception:
        return False


def exit_ip_and_latency(
    proxy_host: str, proxy_port: int, user: str = "", password: str = "", timeout: int = 8
) -> tuple[str, int] | None:
    """通过代理访问 ipify 返回 (出口IP, 延迟ms)。失败返回 None。"""
    auth = f"{user}:{password}@" if user else ""
    proxy_url = f"socks5h://{auth}{proxy_host}:{proxy_port}"
    proxies = {"http": proxy_url, "https": proxy_url}
    try:
        t0 = time.time()
        r = requests.get("https://api.ipify.org", proxies=proxies, timeout=timeout)
        lat = int((time.time() - t0) * 1000)
        ip = r.text.strip()
        return ip, lat
    except Exception:
        return None
