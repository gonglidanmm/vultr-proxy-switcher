"""跨账号替换/添加 IP 的纯逻辑（与 GUI/网络解耦，便于单元测试）。

这些函数不 import PySide6、不做网络请求，输入输出都是纯数据，
单元测试见 tests/test_relay_ops.py。
"""

from __future__ import annotations


def resolve_replace_account(
    target_ws: str,
    host: str,
    line_ws: str,
    accounts: list[dict],
    key_for_host_fn=None,
) -> tuple[str, str]:
    """决定「替换」用哪个账号。返回 (account_name, api_key)。

    优先级：当前 IP 自己记录的账号(target_ws) > host 反查 > 线路级账号(line_ws)。
    全缺失返回 ("", "")，由调用方 fallback 到 active 账号。

    accounts: [{name, api_key}]
    key_for_host_fn: 可选，host -> api_key 的反查函数（测试注入 mock，避免网络）。
    """
    # 1. 当前 IP 记录的账号（IP 级 ws_account）
    if target_ws:
        for a in accounts:
            if a.get("name") == target_ws and a.get("api_key"):
                return target_ws, a.get("api_key", "")
    # 2. host 反查
    if host and key_for_host_fn:
        key = key_for_host_fn(host)
        if key:
            for a in accounts:
                if a.get("api_key") == key:
                    return a.get("name", ""), key
    # 3. 线路级账号
    if line_ws:
        for a in accounts:
            if a.get("name") == line_ws and a.get("api_key"):
                return line_ws, a.get("api_key", "")
    return "", ""


def available_pool(
    all_proxies: list[dict],
    used_keys: set,
    recycled_keys: set,
    cur_hosts: set,
) -> list[dict]:
    """过滤「可替换/可添加」的 IP。

    all_proxies: [{proxy_address, port}]
    used_keys / recycled_keys: (host, port) 集合
    cur_hosts: host 集合（当前线路已有 IP，替换时不能选回自己）

    排除：已中转(且非本线路)、已回收、当前线路候选。
    """
    out = []
    for p in all_proxies:
        host = p.get("proxy_address") or ""
        key = (host, int(p.get("port", 0) or 0))
        if key in used_keys and host not in cur_hosts:
            continue
        if key in recycled_keys:
            continue
        if host in cur_hosts:
            continue
        out.append(p)
    return out


def account_name_for_key(api_key: str, accounts: list[dict]) -> str:
    """api_key -> 账号名（找不到返回空串）。"""
    for a in accounts:
        if a.get("api_key") == api_key:
            return a.get("name", "")
    return ""
