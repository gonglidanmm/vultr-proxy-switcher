"""中转节点：把第三方 SOCKS5 包装成本机加密 SS 中转。

原理：在 Vultr 上跑 gost，监听一个 SS（加密），收到流量后转发到朋友的 SOCKS5。
用户连 SS（加密、直连），出口是朋友的 IP。
"""

from __future__ import annotations

import secrets
import uuid

SS_METHOD = "aes-256-gcm"
RELAY_PORT_START = 45679


def gen_relay_password() -> str:
    return secrets.token_hex(16)


def gen_relay_id() -> str:
    return uuid.uuid4().hex[:8]


def next_relay_port(used_ports: set[int]) -> int:
    p = RELAY_PORT_START
    while p in used_ports:
        p += 1
    return p


def gost_relay_exec(
    relay_password: str,
    relay_port: int,
    host: str,
    port: int,
    user: str,
    password: str,
) -> str:
    return (
        f"/usr/local/bin/gost -L ss://{SS_METHOD}:{relay_password}@:{relay_port} "
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
