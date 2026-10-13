"""持久化配置：JSON 存 ~/.vultr-proxy-switcher/config.json。

只存非敏感/用户可编辑的字段。代理账号密码沿用旧配置作为默认值，
用户可在设置页改。Vultr API 密钥由用户输入。
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

CONFIG_DIR = Path.home() / ".vultr-proxy-switcher"
CONFIG_FILE = CONFIG_DIR / "config.json"

DEFAULTS: dict = {
    # Vultr
    "vultr_api_key": "",
    "label": "vultr-proxy-node",
    "regions": ["nrt", "osa"],
    "plans": ["vc2-1c-1gb", "vhp-1c-1gb-amd"],
    # 远端 gost 代理的账号密码端口（cloud-init 安装时写入）
    "proxy_user": "yduas4980",
    "proxy_pass": "a9966996",
    "proxy_port": 1080,
    # 本地转发代理监听端口
    "local_port": 1080,
    # 手工添加的服务器（不经过 Vultr API 管理，直接当上游用）
    "manual_servers": [],
    # 当前激活的上游 id（托管实例用 instance_id，手工用 "manual:<id>"）
    "active_upstream_id": None,
    # 域名绑定（切换 IP 时自动改 DNS 指向）
    "domain_name": "",
    "cf_token": "",
    "zone_id": "",
    "server_domains": {},  # server_id -> 子域名（每台服务器绑定自己的子域名，如 node1.yduas.cc）
    "domain_counter": 0,  # 子域名自动编号计数（node{counter}.主域名）
    "server_configs": {},  # server_id -> 凭据 dict（每台服务器独立凭据，覆盖全局默认）
    "server_expiry": {},  # server_id -> 到期日期 "YYYY-MM-DD"（客户订阅到期，服务端分配管理）
    # Shadowsocks（加密协议，大陆直连用）
    "ss_password": "",
    "ss_port": 45678,
    # 服务器 root 密码（API 建机时写入，供 SSH 运维）
    "root_password": "",
    # VLESS+Reality（最稳的加密协议，长期用）
    "reality_uuid": "",
    "reality_public_key": "",
    "reality_private_key": "",
    "reality_short_id": "",
    "reality_sni": "www.cloudflare.com",
    "reality_port": 443,
    # Hysteria2（UDP 快协议，抗封锁；mport 为端口跳跃范围）
    "hysteria_password": "",
    "hysteria_port": 8443,
    "hysteria_mport": "20000-20100",
    # 中转节点（把第三方 SOCKS5 包装成本机加密 SS 中转）
    "relays": [],
    # 服务器备注（server_id -> 备注文本，用户手动输入便于区分多台）
    "server_notes": {},
    # 服务器显示名称（server_id -> 自定义名称，本地显示用，不影响 Vultr label / 切换IP）
    "server_names": {},
    # 服务器累计已用流量（域名 -> 累计出站 GB；切换 IP 时旧实例流量累加到域名下，实现"切换后已用继承"）
    "server_cumulative_used": {},
    "server_flow_reset": {},
    # 表格列宽（列索引 -> 宽度，用户手动拖宽后持久化，重开保留）
    "table_column_widths": {},
    "users_table_column_widths": {},  # 用户管理列表列宽
}

_lock = threading.Lock()


def _load() -> dict:
    cfg = dict(DEFAULTS)
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            cfg.update(data)
        except (json.JSONDecodeError, OSError):
            pass
    return cfg


def _save(cfg: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def get(key: str, default=None):
    with _lock:
        return _load().get(key, default)


def get_all() -> dict:
    with _lock:
        return _load()


def set_value(key: str, value) -> None:
    with _lock:
        cfg = _load()
        cfg[key] = value
        _save(cfg)


def update(**kwargs) -> None:
    with _lock:
        cfg = _load()
        cfg.update(kwargs)
        _save(cfg)


def mutate(key: str, fn):
    """原子读-改-写一个 key：fn(当前值) 返回新值。

    防止「读 → 改 → 写」跨锁期间的并发丢失：多线程并发改同一 key 时，
    fn 在锁内拿到的是最新值，写回不会覆盖别的线程的修改。
    fn 只返回「新值」；需要额外结果请通过闭包（可变容器）传出。
    """
    with _lock:
        cfg = _load()
        new_val = fn(cfg.get(key))
        cfg[key] = new_val
        _save(cfg)
        return new_val
