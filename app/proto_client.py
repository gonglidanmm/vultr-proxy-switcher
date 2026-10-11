"""本地协议转换客户端：Reality（xray）+ Hysteria2（hysteria）。

本地起一个 SOCKS5 端口，转发到服务器的 Reality/Hysteria2 端口。
本地代理（127.0.0.1:1080）连这个本地 SOCKS5 端口即可走对应协议。
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import time
from pathlib import Path

_BIN = Path(__file__).resolve().parent.parent / "bin"


class ProtoClient:
    """管理本地 xray/hysteria 子进程。"""

    def __init__(self):
        self._proc: subprocess.Popen | None = None
        self._proto: str | None = None

    @property
    def active_proto(self) -> str | None:
        return self._proto

    def start(self, proto: str, creds: dict, server_ip: str, local_port: int) -> bool:
        """启动协议客户端。返回是否成功。"""
        self.stop()
        if proto == "Reality":
            ok = self._start_xray(creds, server_ip, local_port)
        elif proto == "Hysteria2":
            ok = self._start_hysteria(creds, server_ip, local_port)
        else:
            ok = False
        if ok:
            self._proto = proto
        return ok

    def _start_xray(self, creds: dict, server_ip: str, local_port: int) -> bool:
        cfg = {
            "inbounds": [{
                "listen": "127.0.0.1",
                "port": local_port,
                "protocol": "socks",
                "settings": {"udp": True},
            }],
            "outbounds": [{
                "protocol": "vless",
                "settings": {
                    "vnext": [{
                        "address": server_ip,
                        "port": int(creds.get("reality_port", 443)),
                        "users": [{
                            "id": creds.get("reality_uuid", ""),
                            "encryption": "none",
                            "flow": "xtls-rprx-vision",
                        }],
                    }]
                },
                "streamSettings": {
                    "network": "tcp",
                    "security": "reality",
                    "realitySettings": {
                        "serverName": creds.get("reality_sni", "www.cloudflare.com"),
                        "fingerprint": "chrome",
                        "publicKey": creds.get("reality_public_key", ""),
                        "shortId": creds.get("reality_short_id", ""),
                    },
                },
            }],
        }
        path = Path(tempfile.gettempdir()) / f"vps_xray_{local_port}.json"
        path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        self._proc = subprocess.Popen(
            [str(_BIN / "xray"), "run", "-c", str(path)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        time.sleep(1.2)
        return self._proc.poll() is None

    def _start_hysteria(self, creds: dict, server_host: str, local_port: int) -> bool:
        cfg = (
            f"server: {server_host}:{creds.get('hysteria_port', 8443)}\n"
            f"auth: {creds.get('hysteria_password', '')}\n"
            "socks5:\n"
            f"  listen: 127.0.0.1:{local_port}\n"
            "tls:\n"
            f"  sni: {server_host}\n"
            "  insecure: true\n"
        )
        path = Path(tempfile.gettempdir()) / f"vps_hy_{local_port}.yaml"
        path.write_text(cfg, encoding="utf-8")
        self._proc = subprocess.Popen(
            [str(_BIN / "hysteria"), "client", "-c", str(path)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        time.sleep(1.2)
        return self._proc.poll() is None

    def stop(self) -> None:
        """可靠地停掉当前协议客户端（terminate 后超时则 kill），避免端口残留。"""
        if self._proc is not None:
            proc = self._proc
            self._proc = None
            try:
                if proc.poll() is None:  # 还活着
                    proc.terminate()
                    try:
                        proc.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        try:
                            proc.wait(timeout=3)
                        except Exception:
                            pass
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        self._proto = None
