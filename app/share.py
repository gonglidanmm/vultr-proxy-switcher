"""生成各协议的分享链接，供 PassWall 等一键导入，免手动输入。"""

from __future__ import annotations

import base64


def _b64url(s: str) -> str:
    # 保留 base64 padding「=」（SIP002 标准 websafe-base64 带 padding）。
    # 之前 rstrip("=") 去掉 padding，软路由解码时最后 1 个字符丢失（密码截断）→ SS 连不上。
    return base64.urlsafe_b64encode(s.encode()).decode()


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
    """生成 Hysteria2 分享链接。

    官方 URI 格式：hysteria2://[auth@]host[:port]/[?params][#name]。端口跳跃用 ?mport= 参数
    （v2rayN 实测认 ?mport=20000-20100，不认逗号分隔的端口范围）。mport 为空时省略参数。
    """
    mport_part = f"?mport={mport}" if mport else ""
    return f"hysteria2://{password}@{host}:{port}/{mport_part}#{remark}"
