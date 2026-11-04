"""用户列表的纯数据：合并全部用户、搜索和筛选。不依赖界面。"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

CHIPS = ("全部", "正常", "已禁用", "即将到期", "已过期", "零线路")
SOON_DAYS = 7


def parse_node(node) -> dict:
    if isinstance(node, str):
        try:
            node = json.loads(node)
        except json.JSONDecodeError:
            return {}
    return dict(node or {})


def line_exit_ips(assignment: dict) -> list[str]:
    """一条线路上能搜到的出口地址：当前 IP、入口 host、候选和上游。"""
    node = parse_node(assignment.get("node_data"))
    ips: list[str] = []

    def add(value) -> None:
        text = str(value or "").strip()
        if text and text not in ips:
            ips.append(text)

    add(node.get("ip"))
    add(node.get("host"))
    creds = node.get("_creds") or {}
    for cand in creds.get("candidates") or []:
        add((cand or {}).get("host"))
    add((creds.get("upstream") or {}).get("host"))
    return ips


def line_expiry(assignment: dict) -> str:
    """合并线路用激活候选的到期，否则用 client_expiry。"""
    node = parse_node(assignment.get("node_data"))
    creds = node.get("_creds") or {}
    cands = creds.get("candidates") or []
    try:
        act = int(creds.get("active_idx") or 0)
    except (TypeError, ValueError):
        act = 0
    if len(cands) > 1 and 0 <= act < len(cands):
        ip_exp = str((cands[act] or {}).get("expiry") or "").strip()
        if ip_exp:
            return ip_exp
    return str(node.get("client_expiry") or "").strip()


def _parse_dt(text) -> datetime | None:
    raw = str(text or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1]
    raw = raw.replace("T", " ")
    if "." in raw:
        raw = raw.split(".", 1)[0]
    for fmt, size in (("%Y-%m-%d %H:%M:%S", 19), ("%Y-%m-%d %H:%M", 16), ("%Y-%m-%d", 10)):
        try:
            return datetime.strptime(raw[:size], fmt)
        except ValueError:
            continue
    return None


def earliest_expiry(user: dict) -> datetime | None:
    found = []
    account = _parse_dt(user.get("expires_at"))
    if account:
        found.append(account)
    for line in user.get("lines") or []:
        exp = _parse_dt(line_expiry(line))
        if exp:
            found.append(exp)
    return min(found) if found else None


def merge_user_rows(api_users: list | None, assignments: list | None) -> list[dict]:
    """list_users 里的每个人都在，包括零线路。分配行挂到对应用户。

    分配里出现、用户列表没有的 id 也保留，避免接口短暂不一致时丢线路。
    """
    rows: dict[str, dict] = {}
    order: list[str] = []
    for user in api_users or []:
        if not isinstance(user, dict) or user.get("id") is None:
            continue
        key = str(user.get("id"))
        if key in rows:
            continue
        rows[key] = {
            "user_id": user.get("id"),
            "username": user.get("username") or "",
            "status": user.get("status") or "active",
            "expires_at": user.get("expires_at"),
            "note": user.get("note") or "",
            "switch_limit": int(user.get("switch_limit") or 5),
            "lines": [],
        }
        order.append(key)
    for assignment in assignments or []:
        if not isinstance(assignment, dict) or assignment.get("user_id") is None:
            continue
        key = str(assignment.get("user_id"))
        if key not in rows:
            rows[key] = {
                "user_id": assignment.get("user_id"),
                "username": assignment.get("username") or "",
                "status": assignment.get("user_status") or "active",
                "expires_at": assignment.get("user_expires_at"),
                "note": assignment.get("user_note") or "",
                "switch_limit": int(assignment.get("user_switch_limit") or 5),
                "lines": [],
            }
            order.append(key)
        rows[key]["lines"].append(assignment)
    return [rows[key] for key in order]


def order_users(users: list[dict], saved_ids: list | None) -> list[dict]:
    """按上次拖拽的 id 顺序排，新用户按原顺序接在后面。"""
    by_id = {str(user.get("user_id")): user for user in users}
    ordered = []
    seen = set()
    for saved in saved_ids or []:
        key = str(saved)
        if key in by_id and key not in seen:
            ordered.append(by_id[key])
            seen.add(key)
    for user in users:
        key = str(user.get("user_id"))
        if key not in seen:
            ordered.append(user)
            seen.add(key)
    return ordered


def _query_hit(user: dict, query: str) -> bool:
    if query in str(user.get("username") or "").lower():
        return True
    if query in str(user.get("note") or "").lower():
        return True
    for line in user.get("lines") or []:
        for ip in line_exit_ips(line):
            if query in ip.lower():
                return True
    return False


def _chip_hit(user: dict, chip: str, now: datetime) -> bool:
    status = user.get("status") or "active"
    lines = user.get("lines") or []
    if chip == "正常":
        return status == "active"
    if chip == "已禁用":
        return status != "active"
    if chip == "零线路":
        return len(lines) == 0
    exp = earliest_expiry(user)
    if chip == "已过期":
        return exp is not None and exp < now
    if chip == "即将到期":
        if exp is None or exp < now:
            return False
        return exp <= now + timedelta(days=SOON_DAYS)
    return True


def filter_users(users: list[dict], query: str, chip: str, now: datetime) -> list[dict]:
    """搜索和筛选只决定显示哪些人，不改原列表。"""
    text = (query or "").strip().lower()
    selected = chip or "全部"
    visible = []
    for user in users:
        if text and not _query_hit(user, text):
            continue
        if selected != "全部" and not _chip_hit(user, selected, now):
            continue
        visible.append(user)
    return visible


def free_line_choices(relays: list | None, vultr_servers: list | None, assignments: list | None) -> list[dict]:
    """还没分给人的 relay 和 vultr。返回 {resource_type, resource_id, name, ip}。"""
    taken = set()
    for assignment in assignments or []:
        if not isinstance(assignment, dict):
            continue
        taken.add((assignment.get("resource_type"), str(assignment.get("resource_id"))))
    choices = []
    for relay in relays or []:
        if not isinstance(relay, dict) or relay.get("id") is None:
            continue
        key = ("relay", str(relay.get("id")))
        if key in taken:
            continue
        choices.append({
            "resource_type": "relay",
            "resource_id": str(relay.get("id")),
            "name": relay.get("name") or relay.get("host") or str(relay.get("id")),
            "ip": relay.get("host") or "",
        })
    for server in vultr_servers or []:
        if not isinstance(server, dict) or server.get("id") is None:
            continue
        key = ("vultr", str(server.get("id")))
        if key in taken:
            continue
        choices.append({
            "resource_type": "vultr",
            "resource_id": str(server.get("id")),
            "name": server.get("name") or server.get("label") or str(server.get("id")),
            "ip": server.get("ip") or server.get("main_ip") or "",
        })
    return choices
