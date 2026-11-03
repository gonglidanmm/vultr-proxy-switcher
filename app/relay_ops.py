"""跨账号替换/添加 IP 的纯逻辑（与 GUI/网络解耦，便于单元测试）。

这些函数不 import PySide6、不做网络请求，输入输出都是纯数据，
单元测试见 tests/test_relay_ops.py。
"""

from __future__ import annotations

import json


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
    cur_hosts: host 集合（当前线路已有 IP，一律排除，替换不能选回自己）

    排除：已中转、已回收、当前线路已有 host。国家不在这里过滤。
    """
    out = []
    for p in all_proxies:
        host = p.get("proxy_address") or ""
        key = (host, int(p.get("port", 0) or 0))
        if key in used_keys or key in recycled_keys or host in cur_hosts:
            continue
        out.append(p)
    return out


def account_name_for_key(api_key: str, accounts: list[dict]) -> str:
    """api_key -> 账号名（找不到返回空串）。"""
    for a in accounts:
        if a.get("api_key") == api_key:
            return a.get("name", "")
    return ""


def account_choices(accounts: list[dict] | None) -> list[dict]:
    """设置页账号表、key_for_host、管理 IP 下拉共用的账号列表。

    保留每个有 api_key 的 {name, api_key}，顺序不变；空 key 丢掉。
    """
    out = []
    for a in accounts or []:
        if not isinstance(a, dict):
            continue
        key = (a.get("api_key") or "").strip()
        if not key:
            continue
        out.append({"name": (a.get("name") or "").strip(), "api_key": key})
    return out


def api_key_for_choice(name: str, accounts: list[dict] | None) -> str:
    """下拉框按账号名取 key。重名时用第一个。找不到返回空串。"""
    want = (name or "").strip()
    for a in account_choices(accounts):
        if a["name"] == want:
            return a["api_key"]
    return ""


def _clamp_idx(active_idx, n: int) -> int:
    try:
        idx = int(active_idx or 0)
    except (TypeError, ValueError):
        idx = 0
    if idx < 0 or idx >= n:
        return 0
    return idx


def apply_active_primary(relay, node_data, candidates, active_idx) -> dict | None:
    """把线路 host 和 node_data 的 ip / upstream 指到 candidates[active_idx]。

    空候选返回 None，不改传入对象。下标越界回退到 0。
    配额快照跟激活候选走（和分配记录 quota_gb 一致）。到期仍由调用方写入。
    """
    cands = list(candidates or [])
    if not cands:
        return None
    c = cands[_clamp_idx(active_idx, len(cands))] or {}
    primary = {
        "host": c.get("host", "") or "",
        "port": c.get("port", 0) or 0,
        "user": c.get("user", "") or "",
        "password": c.get("password", "") or "",
        "ws_account": c.get("ws_account", "") or "",
        "quota_gb": float(c.get("quota_gb") or 0),
        "expiry": (c.get("expiry") or "").strip(),
    }
    if relay is not None:
        relay["host"] = primary["host"]
        relay["port"] = primary["port"]
        relay["user"] = primary["user"]
        relay["password"] = primary["password"]
        if primary["ws_account"]:
            relay["ws_account"] = primary["ws_account"]
    if node_data is not None:
        node_data["ip"] = primary["host"]
        node_data["quota_gb"] = primary["quota_gb"]
        if primary["ws_account"]:
            node_data["ws_account"] = primary["ws_account"]
        creds = node_data.get("_creds")
        if not isinstance(creds, dict):
            creds = {}
            node_data["_creds"] = creds
        creds["upstream"] = {
            "host": primary["host"],
            "port": primary["port"],
            "user": primary["user"],
            "password": primary["password"],
            "ws_account": primary["ws_account"],
        }
    return primary


def upstream_changed(old_host, old_port, new_host, new_port) -> bool:
    """主上游地址变了就要重新部署。端口用 int 比，避免 "100" 和 100 误判。"""
    return (old_host or "") != (new_host or "") or _port(old_port) != _port(new_port)


def _port(value):
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _same_endpoint(host, port, old_host, old_port) -> bool:
    if not old_host or (host or "") != old_host:
        return False
    op = _port(old_port)
    if op is None:
        return True
    return _port(port) == op


def pick_replacement(records, old_host: str, old_port) -> dict | None:
    """从 replaced 列表里挑这条旧 IP 最新的一条。对不上返回 None，不猜别的记录。"""
    matches = []
    for rec in records or []:
        if not isinstance(rec, dict):
            continue
        host = rec.get("proxy") or rec.get("proxy_address") or ""
        port = rec.get("proxy_port", rec.get("port"))
        if not _same_endpoint(host, port, old_host, old_port):
            continue
        if not rec.get("replaced_with"):
            continue
        matches.append(rec)
    if not matches:
        return None
    matches.sort(key=lambda r: r.get("created_at") or "")
    return matches[-1]


def endpoint_from_list(proxies, new_host: str, new_port) -> dict | None:
    """用新地址到代理列表里取账号密码。没有用户名或密码就不写回。"""
    for p in proxies or []:
        if not isinstance(p, dict):
            continue
        host = p.get("proxy_address") or ""
        if not _same_endpoint(host, p.get("port"), new_host, new_port):
            continue
        user = p.get("username") or ""
        password = p.get("password") or ""
        port = _port(p.get("port"))
        if not user or not password or port is None:
            return None
        return {"host": host, "port": port, "user": user, "password": password}
    return None


def _complete_endpoint(new) -> dict | None:
    if not isinstance(new, dict):
        return None
    host = new.get("host") or ""
    port = _port(new.get("port"))
    user = new.get("user") or ""
    password = new.get("password") or ""
    if not host or port is None or not user or not password:
        return None
    return {"host": host, "port": port, "user": user, "password": password}


def _swap_endpoint(row: dict, new: dict) -> dict:
    out = dict(row)
    out["host"] = new["host"]
    out["port"] = new["port"]
    out["user"] = new["user"]
    out["password"] = new["password"]
    return out


def _as_node(node_data):
    if isinstance(node_data, str):
        try:
            node_data = json.loads(node_data)
        except json.JSONDecodeError:
            return None
    if not isinstance(node_data, dict):
        return None
    return node_data


def apply_pool_replacement(relays, assignments, old_host: str, old_port, new: dict) -> dict:
    """把 old_host:old_port 换成 new 的地址和凭据。

    只改命中的候选、线路 host、node_data.ip / upstream。
    不改配额、到期、启用、已用，也不把主上游改去另一个候选。
    新凭据不完整时原样返回。不修改传入的 relays。
    """
    endpoint = _complete_endpoint(new)
    unchanged = {
        "relays": list(relays or []),
        "assignments": [],
        "changed_relay_ids": [],
        "deploy_server_ids": [],
    }
    if endpoint is None:
        return unchanged
    new_relays = []
    changed_ids = []
    deploy = []
    for relay in relays or []:
        if not isinstance(relay, dict):
            new_relays.append(relay)
            continue
        before = (relay.get("host"), _port(relay.get("port")))
        changed = False
        r = dict(relay)
        if _same_endpoint(r.get("host"), r.get("port"), old_host, old_port):
            r["host"] = endpoint["host"]
            r["port"] = endpoint["port"]
            r["user"] = endpoint["user"]
            r["password"] = endpoint["password"]
            changed = True
        cands = r.get("candidate_upstreams")
        if cands:
            new_cands = []
            for c in cands:
                if isinstance(c, dict) and _same_endpoint(c.get("host"), c.get("port"), old_host, old_port):
                    new_cands.append(_swap_endpoint(c, endpoint))
                    changed = True
                else:
                    new_cands.append(c)
            r["candidate_upstreams"] = new_cands
        if not changed:
            new_relays.append(relay)
            continue
        new_relays.append(r)
        changed_ids.append(str(r.get("id", "")))
        if (r.get("host"), _port(r.get("port"))) != before:
            sid = r.get("server_id") or ""
            if sid and sid not in deploy:
                deploy.append(sid)
    changed_assignments = []
    for a in assignments or []:
        if not isinstance(a, dict):
            continue
        if a.get("resource_type") not in (None, "", "relay"):
            continue
        nd = _as_node(a.get("node_data"))
        if nd is None:
            continue
        patched, touched = _patch_node(nd, old_host, old_port, endpoint)
        if touched:
            copied = dict(a)
            copied["node_data"] = patched
            changed_assignments.append(copied)
    return {
        "relays": new_relays,
        "assignments": changed_assignments,
        "changed_relay_ids": changed_ids,
        "deploy_server_ids": deploy,
    }


def _patch_node(nd: dict, old_host: str, old_port, new: dict) -> tuple[dict, bool]:
    out = dict(nd)
    creds = dict(out.get("_creds") or {}) if isinstance(out.get("_creds"), dict) else {}
    touched = False
    cands = creds.get("candidates")
    if cands:
        new_cands = []
        for c in cands:
            if isinstance(c, dict) and _same_endpoint(c.get("host"), c.get("port"), old_host, old_port):
                new_cands.append(_swap_endpoint(c, new))
                touched = True
            else:
                new_cands.append(c)
        creds["candidates"] = new_cands
    up = dict(creds.get("upstream") or {}) if isinstance(creds.get("upstream"), dict) else {}
    if _same_endpoint(up.get("host"), up.get("port"), old_host, old_port):
        up["host"] = new["host"]
        up["port"] = new["port"]
        up["user"] = new["user"]
        up["password"] = new["password"]
        creds["upstream"] = up
        touched = True
        if (out.get("ip") or "") == old_host:
            out["ip"] = new["host"]
    elif (out.get("ip") or "") == old_host and not up.get("host"):
        out["ip"] = new["host"]
        touched = True
    if touched:
        out["_creds"] = creds
    return out, touched
