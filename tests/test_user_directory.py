"""用户列表：零线路也要出现，搜索和筛选不改统计用的全量。"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.user_directory import (  # noqa: E402
    filter_users,
    free_line_choices,
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


def test_filter_chips():
    rows = merge_user_rows(
        [
            {"id": 1, "username": "zero", "status": "active", "note": "", "expires_at": "2026-06-20", "switch_limit": 5},
            {"id": 2, "username": "off", "status": "disabled", "note": "", "expires_at": None, "switch_limit": 5},
            {"id": 3, "username": "late", "status": "active", "note": "", "expires_at": "2026-06-01", "switch_limit": 5},
            {"id": 4, "username": "ok", "status": "active", "note": "", "expires_at": "2026-12-01", "switch_limit": 5},
        ],
        [],
    )
    assert [u["username"] for u in filter_users(rows, "", "零线路", NOW)] == ["zero", "off", "late", "ok"]
    assert [u["username"] for u in filter_users(rows, "", "已禁用", NOW)] == ["off"]
    assert [u["username"] for u in filter_users(rows, "", "即将到期", NOW)] == ["zero"]
    assert [u["username"] for u in filter_users(rows, "", "已过期", NOW)] == ["late"]
    assert [u["username"] for u in filter_users(rows, "", "正常", NOW)] == ["zero", "late", "ok"]


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
