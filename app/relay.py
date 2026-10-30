"""中转节点：把第三方 SOCKS5 包装成本机加密 SS + SOCKS5 双协议中转。

原理：在 Vultr 上跑 gost，同时监听 SS（加密）和 SOCKS5（带认证）两个入站，
收到流量后转发到 webshare 的 SOCKS5 上游。
- 客户连 SS（aes-256-gcm 加密、直连穿透 GFW）——大陆客户主力。
- 客户连 SOCKS5（明文带认证）——海外/软路由客户用。
出口都是 webshare 的 IP。
"""

from __future__ import annotations

import re
import secrets
import uuid

SS_METHOD = "aes-256-gcm"
RELAY_PORT_START = 45679       # SS 入站端口起始
SOCKS_PORT_OFFSET = 10000      # SOCKS5 入站端口 = SS 端口 + 10000（55679 起）
SOCKS_USER = "proxy"           # SOCKS5 入站认证用户名


def gen_relay_password() -> str:
    return secrets.token_hex(16)


def gen_relay_id() -> str:
    return uuid.uuid4().hex[:8]


def next_relay_port(used_ports: set[int], start: int | None = None) -> int:
    """分配 relay 端口。start 传入历史最高端口+1，实现单调递增不复用（防旧链接串号）。"""
    p = RELAY_PORT_START if start is None else max(RELAY_PORT_START, start)
    while p in used_ports:
        p += 1
    return p


def socks_port(relay_port: int) -> int:
    """SS 端口对应的 SOCKS5 入站端口。"""
    return relay_port + SOCKS_PORT_OFFSET


REALITY_PORT_OFFSET = 8000    # Reality 入站端口 = SS 端口 + 8000（53679 起，< 65535）
HYSTERIA_PORT_OFFSET = 12000  # Hysteria2 入站端口 = SS 端口 + 12000（57679 起，< 65535，UDP）


def reality_port(relay_port: int) -> int:
    """SS 端口对应的 Reality 入站端口。"""
    return relay_port + REALITY_PORT_OFFSET


def hysteria_port(relay_port: int) -> int:
    """SS 端口对应的 Hysteria2 入站端口（UDP）。"""
    return relay_port + HYSTERIA_PORT_OFFSET


def gost_relay_exec(
    relay_password: str,
    relay_port: int,
    host: str,
    port: int,
    user: str,
    password: str,
) -> str:
    sp = socks_port(relay_port)
    return (
        f"/usr/local/bin/gost "
        f"-L ss://{SS_METHOD}:{relay_password}@:{relay_port} "
        f"-L socks5://{SOCKS_USER}:{relay_password}@:{sp} "
        f"-F socks5://{user}:{password}@{host}:{port}"
    )


def relay_unit(
    name: str,
    relay_password: str,
    relay_port: int,
    host: str,
    port: int,
    user: str,
    password: str,
) -> str:
    exec_line = gost_relay_exec(relay_password, relay_port, host, port, user, password)
    return f"""[Unit]
Description=gost relay {name}
After=network.target
[Service]
ExecStart={exec_line}
Restart=always
RestartSec=3
LimitNOFILE=65535
[Install]
WantedBy=multi-user.target
"""


def build_singbox_config(relays: list[dict]) -> str:
    """生成 sing-box 配置：每个 relay 一个 Reality 入站 + 一个 Hysteria2 入站，路由到各自 webshare 上游。"""
    import json

    inbounds: list[dict] = []
    outbounds: list[dict] = []
    rules: list[dict] = []
    for r in relays:
        rp = int(r.get("relay_port", 0))
        rid = r.get("id", "")
        sni = r.get("reality_sni", "www.cloudflare.com")
        # Reality（VLESS + XTLS-Vision）入站
        inbounds.append({
            "type": "vless",
            "tag": f"reality-{rid}",
            "listen": "0.0.0.0",
            "listen_port": reality_port(rp),
            "users": [{"uuid": r.get("reality_uuid", ""), "flow": "xtls-rprx-vision"}],
            "tls": {
                "enabled": True,
                "server_name": sni,
                "reality": {
                    "enabled": True,
                    "handshake": {"server": sni, "server_port": 443},
                    "private_key": r.get("reality_private_key", ""),
                    "short_id": [r.get("reality_short_id", "")],
                },
            },
        })
        # Hysteria2 入站（UDP，必须 TLS；不配 obfs——v2rayN 等客户端导入链接不带 obfs，配了会握手失败）
        inbounds.append({
            "type": "hysteria2",
            "tag": f"hysteria2-{rid}",
            "listen": "0.0.0.0",
            "listen_port": hysteria_port(rp),
            "tls": {
                "enabled": True,
                "certificate_path": "/etc/sing-box/cert.pem",
                "key_path": "/etc/sing-box/key.pem",
            },
            "users": [{"password": r.get("hysteria_password", "")}],
        })
        # 出站到 webshare 上游（SOCKS5）
        outbounds.append({
            "type": "socks",
            "tag": f"out-{rid}",
            "server": r.get("host", ""),
            "server_port": int(r.get("port", 0)),
            "version": "5",
            "username": r.get("user", ""),
            "password": r.get("password", ""),
            # webshare 上游不支持 SOCKS5 UDP ASSOCIATE（返回 code=7），
            # 加 udp_over_tcp 把 UDP（QUIC/DNS）封装进 TCP，否则 tiktok 的 QUIC 视频流失败
            "udp_over_tcp": True,
        })
        rules.append({"inbound": [f"reality-{rid}", f"hysteria2-{rid}"], "outbound": f"out-{rid}"})

    config = {
        "log": {"level": "warn"},
        "inbounds": inbounds,
        "outbounds": outbounds,
        "route": {"rules": rules},
    }
    return json.dumps(config, ensure_ascii=False, indent=2)


def build_singbox_relay_config(r: dict) -> str:
    """单 relay 的 sing-box 配置：1 个 Reality + 1 个 Hysteria2 入站 + 1 个 outbound。

    每个 relay 一个独立进程（sing-box-relay-<id>.service），切换上游只重启这一个进程，
    不影响其他 relay 的 Reality/Hysteria2 连接（隔离）。
    """
    import json

    rp = int(r.get("relay_port", 0))
    sni = r.get("reality_sni", "www.cloudflare.com")
    inbounds = [
        {
            "type": "vless",
            "tag": "reality",
            "listen": "0.0.0.0",
            "listen_port": reality_port(rp),
            "users": [{"uuid": r.get("reality_uuid", ""), "flow": "xtls-rprx-vision"}],
            "tls": {
                "enabled": True,
                "server_name": sni,
                "reality": {
                    "enabled": True,
                    "handshake": {"server": sni, "server_port": 443},
                    "private_key": r.get("reality_private_key", ""),
                    "short_id": [r.get("reality_short_id", "")],
                },
            },
        },
        {
            "type": "hysteria2",
            "tag": "hysteria2",
            "listen": "0.0.0.0",
            "listen_port": hysteria_port(rp),
            "tls": {
                "enabled": True,
                "certificate_path": "/etc/sing-box/cert.pem",
                "key_path": "/etc/sing-box/key.pem",
            },
            "users": [{"password": r.get("hysteria_password", "")}],
        },
    ]
    outbounds = [
        {
            "type": "socks",
            "tag": "out",
            "server": r.get("host", ""),
            "server_port": int(r.get("port", 0)),
            "version": "5",
            "username": r.get("user", ""),
            "password": r.get("password", ""),
            # webshare 上游不支持 SOCKS5 UDP ASSOCIATE（code=7），udp_over_tcp 封装 QUIC/DNS
            "udp_over_tcp": True,
        }
    ]
    config = {
        "log": {"level": "warn"},
        "inbounds": inbounds,
        "outbounds": outbounds,
        "route": {"rules": [{"inbound": ["reality", "hysteria2"], "outbound": "out"}]},
    }
    return json.dumps(config, ensure_ascii=False, indent=2)


def singbox_relay_unit(relay_id: str) -> str:
    """单 relay 的 sing-box systemd unit。"""
    return f"""[Unit]
Description=sing-box relay {relay_id}
After=network.target
[Service]
ExecStart=/usr/local/bin/sing-box run -c /etc/sing-box/relay-{relay_id}.json
Restart=always
RestartSec=3
LimitNOFILE=65535
[Install]
WantedBy=multi-user.target
"""


_CITY_EN2CN = {
    "dallas": "达拉斯",
    "ashburn": "阿什本",
    "asburn": "阿什本",
    "buffalo": "布法罗",
    "charlotte": "夏洛特",
    "los angeles": "洛杉矶",
    "la": "洛杉矶",
    "new york": "纽约",
    "ny": "纽约",
    "san francisco": "旧金山",
    "boca raton": "博卡拉顿",
}
_CN_CITIES = ["纽约", "达拉斯", "阿什本", "博卡拉顿", "旧金山", "夏洛特", "布法罗", "洛杉矶"]


def _relay_location(name: str) -> str:
    """从 relay name 提取「国家·城市」（如 数据中心-US-Dallas-01 → 美国·达拉斯）。"""
    if not name:
        return ""
    for cn in _CN_CITIES:
        if cn in name:
            return f"美国·{cn}"
    m = re.search(r"US[- ]([A-Za-z]+(?:\s+[A-Za-z]+)?)", name)
    if m:
        key = m.group(1).lower()
        return f"美国·{_CITY_EN2CN.get(key, m.group(1).title())}"
    return ""


def relay_node_data(relay: dict, server_ip: str, switch_api_key: str = "") -> dict:
    """构建 relay 的 node_data 快照（与 vultr 同构，客户端零改动渲染）。

    契约见 skill references/relay-assignment-d1-model.md：
    - host = entry_domain（客户连接用域名，切 IP 不变）
    - _creds.switch = 前置服务器 switch_server.py 地址 + api_key（Worker /stop、/traffic 用）
    - _creds.upstream = 当前 webshare 上游凭据
    - client_expiry / quota_gb 由分配弹窗（用户管理页）写，这里不填
    """
    from .share import hysteria2_link, socks5_link, ss_link, vless_reality_link

    rp = int(relay.get("relay_port", 0))
    relay_pw = relay.get("relay_password", "")
    entry = relay.get("entry_domain", "") or server_ip
    name = relay.get("name", "")
    ruuid = relay.get("reality_uuid", "")
    rpub = relay.get("reality_public_key", "")
    rsid = relay.get("reality_short_id", "")
    rsni = relay.get("reality_sni", "www.cloudflare.com")
    hyst_pw = relay.get("hysteria_password", "")
    sp = socks_port(rp)
    rp_real = reality_port(rp)
    hp = hysteria_port(rp)

    return {
        "host": entry,
        "socks5": {"port": sp, "username": SOCKS_USER, "password": relay_pw},
        "ss": {"port": rp, "method": SS_METHOD, "password": relay_pw},
        "reality": {"port": rp_real, "uuid": ruuid, "public_key": rpub, "short_id": rsid,
                    "sni": rsni, "flow": "xtls-rprx-vision", "fp": "chrome"},
        "hysteria2": {"port": hp, "password": hyst_pw, "mport": ""},
        "links": {
            "socks5": socks5_link(entry, sp, SOCKS_USER, relay_pw, "SOCKS5"),
            "ss": ss_link(entry, rp, SS_METHOD, relay_pw, "SS"),
            "reality": vless_reality_link(entry, rp_real, ruuid, "xtls-rprx-vision", rsni,
                                          "chrome", rpub, rsid, "Reality"),
            "hysteria2": hysteria2_link(entry, hp, hyst_pw, "", "Hysteria2"),
        },
        "name": name,
        "location": relay.get("location", "") or _relay_location(name),
        "ip": relay.get("host", ""),  # 上游出口 IP（客户端「当前 IP」列）
        "domain": entry,
        "ip_type": relay.get("ip_type", "dc"),
        "_creds": {
            "relay_id": relay.get("id", ""),
            "server_id": relay.get("server_id", ""),
            "switch": {"server_ip": entry, "port": 8080, "api_key": switch_api_key},
            "upstream": {
                "host": relay.get("host", ""),
                "port": relay.get("port", ""),
                "user": relay.get("user", ""),
                "password": relay.get("password", ""),
            },
        },
    }
