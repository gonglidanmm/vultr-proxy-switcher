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


def _rel_short(exp: datetime, now: datetime) -> str:
    secs = int((exp - now).total_seconds())
    if secs < 3600:
        return f"{max(secs, 0) // 60}分钟"
    if secs < 86400:
        return f"{secs // 3600}小时"
    return f"{secs // 86400}天"


def line_overview(user: dict, now: datetime) -> dict:
    """线路概况。账号状态不算进来。

    已到期只统计线路。最近到期只看还没到期的线路。
    没有线路时 text 是「无线路」，不要写成「已过期」。
    """
    lines = user.get("lines") or []
    if not lines:
        return {"text": "无线路", "active": 0, "expired": 0, "soonest": None, "soon_text": ""}
    active = 0
    expired = 0
    active_expiries: list[datetime] = []
    for line in lines:
        exp = _parse_dt(line_expiry(line))
        if exp is not None and exp < now:
            expired += 1
            continue
        active += 1
        if exp is not None:
            active_expiries.append(exp)
    soonest = min(active_expiries) if active_expiries else None
    soon_text = _rel_short(soonest, now) if soonest else ""
    parts = [f"在用 {active}"]
    if expired:
        parts.append(f"已到期 {expired}")
    if soon_text:
        parts.append(f"最近 {soon_text}")
    return {
        "text": " · ".join(parts),
        "active": active,
        "expired": expired,
        "soonest": soonest,
        "soon_text": soon_text,
    }


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


def place_user(users: list[dict], visible: list[dict], src_row: int, dst_row: int) -> list[dict]:
    """把可见列表的 src_row 放到 dst_row 那一行。没动时返回原列表。"""
    if (
        src_row < 0
        or dst_row < 0
        or src_row >= len(visible)
        or dst_row >= len(visible)
        or src_row == dst_row
    ):
        return users
    moved_id = visible[src_row].get("user_id")
    target_id = visible[dst_row].get("user_id")
    src = next((i for i, user in enumerate(users) if user.get("user_id") == moved_id), None)
    dst = next((i for i, user in enumerate(users) if user.get("user_id") == target_id), None)
    if src is None or dst is None or src == dst:
        return users
    items = list(users)
    item = items.pop(src)
    # pop 之后仍用原来的目标下标插入，拖到哪一行就落在哪一行。
    items.insert(dst, item)
    return items


def effective_switch_limit(node_limit, user_limit) -> int:
    """节点单独设过就用节点的，否则用用户的，都没有则每天 5。"""
    node = int(node_limit or 0)
    user = int(user_limit or 0)
    if node > 0:
        return node
    return user if user > 0 else 5


def switch_usage_label(count, switch_date, today, limit) -> str:
    """已用 IP 含初始那一个。日期不是今天就从 0 计。"""
    shown = int(count or 0)
    date = str(switch_date or "")[:10]
    if date and date != str(today):
        shown = 0
    return f"已用 IP：{shown + 1}/{int(limit)}"


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
    overview = line_overview(user, now)
    if chip == "已过期":
        return overview["expired"] >= 1
    if chip == "即将到期":
        soonest = overview["soonest"]
        if soonest is None:
            return False
        return soonest <= now + timedelta(days=SOON_DAYS)
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
