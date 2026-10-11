"""生成各协议的分享链接，供 PassWall 等一键导入，免手动输入。"""

from __future__ import annotations

import base64


def _b64url(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode()).decode().rstrip("=")


def socks5_link(host: str, port: int, user: str, password: str, remark: str = "SOCKS5") -> str:
    return f"socks5://{user}:{password}@{host}:{port}#{remark}"


def ss_link(host: str, port: int, method: str, password: str, remark: str = "Shadowsocks") -> str:
    return f"ss://{_b64url(f'{method}:{password}')}@{host}:{port}#{remark}"


def vless_reality_link(
    host: str,
    port: int,
    uuid: str,
    flow: str,
    sni: str,
    fp: str,
    public_key: str,
    short_id: str,
    remark: str = "VLESS-Reality",
) -> str:
    return (
        f"vless://{uuid}@{host}:{port}?encryption=none&flow={flow}"
        f"&security=reality&sni={sni}&fp={fp}&pbk={public_key}&sid={short_id}&type=tcp#{remark}"
    )


def hysteria2_link(
    host: str,
    port: int,
    password: str,
    mport: str = "",
    remark: str = "Hysteria2",
) -> str:
    """生成 Hysteria2 分享链接（官方格式：密码不编码；正式证书无需 insecure；mport 为端口跳跃范围）。"""
    params = f"?mport={mport}" if mport else ""
    return f"hysteria2://{password}@{host}:{port}/{params}#{remark}"
