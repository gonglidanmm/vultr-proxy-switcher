"""端到端延迟检测：本机跑各协议客户端，测真实翻墙延迟（非 TCP 端口延迟）。"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
from pathlib import Path

_BIN_DIR = Path(__file__).resolve().parent.parent / "bin"

# 各协议的本地代理端口（检测时临时占用，避免冲突）
_PORTS = {"SOCKS5": 20800, "SS": 20801, "Reality": 20802, "Hysteria2": 20803}


def _socks5_e2e(port: int, timeout: int = 10) -> int | None:
    """通过本地 SOCKS5 代理访问 ipify，返回延迟(ms) 或 None。"""
    try:
        import ssl

        import socks

        s = socks.socksocket()
        s.settimeout(timeout)
        s.set_proxy(socks.SOCKS5, "127.0.0.1", port)
        t0 = time.time()
        s.connect(("api.ipify.org", 443))
        ctx = ssl.create_default_context()
        ss = ctx.wrap_socket(s, server_hostname="api.ipify.org")
        ss.sendall(b"GET / HTTP/1.1\r\nHost: api.ipify.org\r\nConnection: close\r\n\r\n")
        data = b""
        while True:
            c = ss.recv(4096)
            if not c:
                break
            data += c
        return int((time.time() - t0) * 1000)
    except Exception:
        return None


def _port_ready(port: int, timeout: float = 0.5) -> bool:
    """检测本地端口是否已监听（客户端启动就绪判定）。"""
    import socket

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(("127.0.0.1", port))
        return True
    except Exception:
        return False
    finally:
        s.close()


def _run(cmd: list, wait: float = 1.5):
    p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(wait)
    return p


def _stop(p) -> None:
    try:
        p.terminate()
        p.wait(timeout=3)
    except Exception:
        try:
            p.kill()
        except Exception:
            pass


def _gost_e2e(upstream: str, local_port: int) -> int | None:
    """用 gost 做客户端，转发到上游，测端到端延迟。"""
    gost = _BIN_DIR / "gost"
    if not gost.exists():
        return None
    p = _run([str(gost), "-L", f"socks5://:{local_port}", "-F", upstream])
    try:
        for _ in range(20):
            if _port_ready(local_port):
                break
            time.sleep(0.5)
        return _socks5_e2e(local_port)
    finally:
        _stop(p)


def _xray_reality_e2e(host: str, port: int, uuid: str, public_key: str, short_id: str, sni: str, local_port: int) -> int | None:
    """用 xray 做 VLESS+Reality 客户端，测端到端延迟。"""
    xray = _BIN_DIR / "xray"
    if not xray.exists():
        return None
    conf = {
        "log": {"loglevel": "error"},
        "inbounds": [
            {"listen": "127.0.0.1", "port": local_port, "protocol": "socks", "settings": {"udp": True}}
        ],
        "outbounds": [
            {
                "protocol": "vless",
                "settings": {
                    "vnext": [
                        {
                            "address": host,
                            "port": port,
                            "users": [{"id": uuid, "flow": "xtls-rprx-vision", "encryption": "none"}],
                        }
                    ]
                },
                "streamSettings": {
                    "network": "tcp",
                    "security": "reality",
                    "realitySettings": {
                        "serverName": sni,
                        "publicKey": public_key,
                        "shortId": short_id,
                        "fingerprint": "chrome",
                    },
                },
            }
        ],
    }
    fd, path = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump(conf, f)
    p = _run([str(xray), "run", "-c", path])
    try:
        for _ in range(20):
            if _port_ready(local_port):
                break
            time.sleep(0.5)
        return _socks5_e2e(local_port)
    finally:
        _stop(p)
        try:
            os.remove(path)
        except OSError:
            pass


def _hysteria_e2e(host: str, port: int, password: str, local_port: int) -> int | None:
    """用 hysteria 做 Hysteria2 客户端，测端到端延迟。"""
    hy = _BIN_DIR / "hysteria"
    if not hy.exists():
        return None
    conf = f"""server: {host}:{port}
auth: {password}
socks5:
  listen: 127.0.0.1:{local_port}
"""
    fd, path = tempfile.mkstemp(suffix=".yaml")
    with os.fdopen(fd, "w") as f:
        f.write(conf)
    p = _run([str(hy), "client", "-c", path])
    try:
        # hysteria 客户端启动较慢（~2 秒才 SOCKS5 就绪），轮询等待最多 10 秒
        for _ in range(20):
            if _port_ready(local_port):
                break
            time.sleep(0.5)
        return _socks5_e2e(local_port)
    finally:
        _stop(p)
        try:
            os.remove(path)
        except OSError:
            pass


def e2e_latencies(
    host: str,
    socks_user: str,
    socks_pass: str,
    socks_port: int,
    ss_pass: str,
    ss_port: int,
    reality_uuid: str,
    reality_public_key: str,
    reality_short_id: str,
    reality_sni: str,
    reality_port: int,
    hy_pass: str,
    hy_port: int,
    domain: str = "",
) -> dict:
    """测每个协议的端到端翻墙延迟，返回 {协议名: 延迟ms 或 None}。"""
    results: dict = {}
    results["SOCKS5"] = _gost_e2e(
        f"socks5://{socks_user}:{socks_pass}@{host}:{socks_port}", _PORTS["SOCKS5"]
    )
    results["SS"] = _gost_e2e(f"ss://aes-256-gcm:{ss_pass}@{host}:{ss_port}", _PORTS["SS"])
    results["Reality"] = _xray_reality_e2e(
        host, reality_port, reality_uuid, reality_public_key, reality_short_id, reality_sni, _PORTS["Reality"]
    )
    # Hysteria2 证书绑定域名，须用域名连接（否则证书域名不匹配）
    hy_host = domain or host
    results["Hysteria2"] = _hysteria_e2e(hy_host, hy_port, hy_pass, _PORTS["Hysteria2"])
    return results
