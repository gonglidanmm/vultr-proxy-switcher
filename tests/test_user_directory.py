"""用户列表：零线路也要出现，搜索和筛选不改统计用的全量。"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.user_directory import (  # noqa: E402
    filter_users,
    free_line_choices,
    line_overview,
    merge_user_rows,
    order_users,
)

NOW = datetime(2026, 6, 15, 12, 0, 0)


def test_merge_keeps_user_with_zero_lines():
    users = [
        {"id": 1, "username": "empty", "status": "active", "note": "新客", "expires_at": None, "switch_limit": 5},
        {"id": 2, "username": "busy", "status": "disabled", "note": "", "expires_at": "2026-07-01", "switch_limit": 5},
    ]
    assignments = [{
        "user_id": 2,
        "username": "busy",
        "user_status": "disabled",
        "user_note": "",
        "user_expires_at": "2026-07-01",
        "resource_id": "r1",
        "node_data": {"ip": "1.1.1.1"},
    }]
    rows = merge_user_rows(users, assignments)
    assert [row["username"] for row in rows] == ["empty", "busy"]
    assert rows[0]["lines"] == []
    assert rows[1]["lines"][0]["resource_id"] == "r1"
    assert len(rows) == 2


def test_filter_search_exit_ip_and_note_keeps_full_list():
    rows = merge_user_rows(
        [
            {"id": 1, "username": "ann", "status": "active", "note": "达拉斯", "switch_limit": 5},
            {"id": 2, "username": "bob", "status": "active", "note": "", "switch_limit": 5},
        ],
        [{
            "user_id": 2,
            "resource_id": "r2",
            "node_data": {"ip": "8.8.8.8", "client_expiry": "2026-12-01 00:00:00"},
        }],
    )
    by_note = filter_users(rows, "达拉斯", "全部", NOW)
    by_ip = filter_users(rows, "8.8.8", "全部", NOW)
    assert [u["username"] for u in by_note] == ["ann"]
    assert [u["username"] for u in by_ip] == ["bob"]
    assert len(rows) == 2


def _line(expiry: str, ip: str = "1.1.1.1") -> dict:
    return {"resource_id": ip, "node_data": {"ip": ip, "client_expiry": expiry}}


def test_line_overview_mixes_expired_and_active():
    """一条线路过期不能把整个用户写成「已过期」。最近到期只看还在用的线路。"""
    rows = merge_user_rows(
        [{"id": 1, "username": "mix", "status": "active", "expires_at": "2020-01-01", "switch_limit": 5}],
        [
            {"user_id": 1, **_line("2026-06-01 00:00:00", "1.1.1.1")},
            {"user_id": 1, **_line("2026-06-20 12:00:00", "2.2.2.2")},
        ],
    )
    overview = line_overview(rows[0], NOW)
    assert overview["active"] == 1
    assert overview["expired"] == 1
    assert overview["text"] == "在用 1 · 已到期 1 · 最近 5天"
    assert "已过期" not in overview["text"]
    assert filter_users(rows, "", "已过期", NOW) == rows
    assert filter_users(rows, "", "即将到期", NOW) == rows
    assert filter_users(rows, "", "正常", NOW) == rows


def test_line_overview_zero_lines():
    rows = merge_user_rows(
        [{"id": 1, "username": "zero", "status": "active", "expires_at": "2026-06-01", "switch_limit": 5}],
        [],
    )
    assert line_overview(rows[0], NOW)["text"] == "无线路"
    assert filter_users(rows, "", "已过期", NOW) == []
    assert filter_users(rows, "", "即将到期", NOW) == []
    assert filter_users(rows, "", "零线路", NOW) == rows


def test_filter_chips():
    rows = merge_user_rows(
        [
            {"id": 1, "username": "zero", "status": "active", "note": "", "expires_at": "2026-06-20", "switch_limit": 5},
            {"id": 2, "username": "off", "status": "disabled", "note": "", "expires_at": None, "switch_limit": 5},
            {"id": 3, "username": "late", "status": "active", "note": "", "expires_at": None, "switch_limit": 5},
            {"id": 4, "username": "soon", "status": "active", "note": "", "expires_at": None, "switch_limit": 5},
        ],
        [
            {"user_id": 3, **_line("2026-06-01 00:00:00", "3.3.3.3")},
            {"user_id": 4, **_line("2026-06-18 00:00:00", "4.4.4.4")},
        ],
    )
    assert [u["username"] for u in filter_users(rows, "", "零线路", NOW)] == ["zero", "off"]
    assert [u["username"] for u in filter_users(rows, "", "已禁用", NOW)] == ["off"]
    assert [u["username"] for u in filter_users(rows, "", "即将到期", NOW)] == ["soon"]
    assert [u["username"] for u in filter_users(rows, "", "已过期", NOW)] == ["late"]
    assert [u["username"] for u in filter_users(rows, "", "正常", NOW)] == ["zero", "late", "soon"]


def test_order_users_appends_unknown_ids():
    rows = merge_user_rows(
        [{"id": 3, "username": "c", "status": "active", "switch_limit": 5},
         {"id": 1, "username": "a", "status": "active", "switch_limit": 5}],
        [],
    )
    ordered = order_users(rows, [1, 9])
    assert [u["username"] for u in ordered] == ["a", "c"]


def test_free_line_choices_skip_assigned():
    choices = free_line_choices(
        [{"id": "r1", "name": "已分", "host": "1.1.1.1"}, {"id": "r2", "name": "空闲", "host": "2.2.2.2"}],
        [{"id": "v1", "label": "机", "main_ip": "9.9.9.9"}, {"id": "v2", "name": "已用", "ip": "3.3.3.3"}],
        [{"resource_type": "relay", "resource_id": "r1"}, {"resource_type": "vultr", "resource_id": "v2"}],
    )
    assert [(c["resource_type"], c["resource_id"]) for c in choices] == [("relay", "r2"), ("vultr", "v1")]
    assert choices[1]["ip"] == "9.9.9.9"
