"""Vultr 重置流量基准，以及孤儿实例对照。

公开边界（纯函数，不碰界面、网络、删除）：

- with_flow_reset_baseline：把「当前出站 GB」写进 server_flow_reset。
  能抓住 5 分钟同步用 used-baseline 把刚清零的流量盖回去。
  抓不住按钮有没有调用 reset_vultr_flow。
- known_instance_ids：本地配置和 D1 分配里算「已登记」的实例 id。
  能抓住手工机、凭据、域名和 vultr 分配。抓不住界面有没有拿全量配置去调它。
- billing_review_rows：删除失败记录对照实时实例，再加上账户里两边都不认识的实例。
  能抓住「旧 id 还在不在、未登记的要不要列出来、分配列表失败时不许把正常机器列成可删」。
  抓不住确认框，也抓不住删除按钮会不会去切换 IP。

调用点另有一条源码回归：用户页 _reset_flow 必须走 reset_vultr_flow，并继续写 server_flow_reset。
relay 的 reset_flow 对 Vultr 实例返回「分配不存在」。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.vultr import (
    billing_review_rows,
    known_instance_ids,
    with_flow_reset_baseline,
)

LIVE = [
    {
        "id": "old-1",
        "label": "node",
        "main_ip": "1.2.3.4",
        "region": "nrt",
        "date_created": "2026-10-01",
    },
    {
        "id": "stray",
        "label": "loose",
        "main_ip": "9.9.9.9",
        "region": "ewr",
        "date_created": "2026-10-02",
    },
    {
        "id": "kept",
        "label": "ok",
        "main_ip": "8.8.8.8",
        "region": "lax",
        "date_created": "2026-10-03",
    },
    {
        "id": "ctrl",
        "label": "yduas-IPV6",
        "main_ip": "100.64.1.2",
        "region": "ewr",
        "date_created": "2026-10-04",
    },
]

RECORDS = [
    {
        "old_id": "old-1",
        "new_id": "new-1",
        "http_status": 500,
        "created_at": "2026-11-01T00:00:00Z",
    },
    {
        "old_id": "gone-1",
        "new_id": "new-2",
        "http_status": 403,
        "created_at": "2026-10-01T00:00:00Z",
    },
]


def test_flow_reset_baseline_is_current_outgoing_gb():
    """入站不计。1.5GB + 0.5GB 出站 = 2GB，同步时 used-baseline 为 0。"""
    days = {
        "2026-11-01": {"incoming_bytes": 9_000_000_000, "outgoing_bytes": 1_500_000_000},
        "2026-11-02": {"outgoing_bytes": 500_000_000},
    }
    updated = with_flow_reset_baseline({"other": 1.0}, "inst-1", days)
    assert updated["inst-1"] == 2.0
    assert updated["other"] == 1.0
    assert max(0.0, 2.0 - updated["inst-1"]) == 0.0


def test_flow_reset_baseline_is_zero_when_bandwidth_missing():
    updated = with_flow_reset_baseline(None, "inst-1", None)
    assert updated == {"inst-1": 0.0}


def test_flow_reset_baseline_does_not_mutate_the_previous_map():
    original = {"inst-1": 9.0}
    with_flow_reset_baseline(original, "inst-1", {"d": {"outgoing_bytes": 1_000_000_000}})
    assert original == {"inst-1": 9.0}


def test_known_ids_cover_local_config_manual_servers_and_vultr_assignments():
    local = {
        "server_configs": {"cfg-1": {}},
        "server_domains": {"dom-1": "a.example"},
        "server_notes": {"note-1": "n"},
        "server_names": {"name-1": "n"},
        "server_expiry": {"exp-1": "2026-01-01"},
        "server_cumulative_used": {"cum-1": 1},
        "server_flow_reset": {"reset-1": 1},
        "server_order": ["order-1"],
        "active_upstream_id": "active-1",
        "manual_servers": [{"id": "man1", "host": "10.0.0.1"}],
        "relays": [{"server_id": "relay-host-1"}],
    }
    assignments = [
        {"resource_type": "vultr", "resource_id": "d1-1"},
        {"resource_id": "legacy-1"},
        {"resource_type": "relay", "resource_id": "inst-relay"},
    ]
    ids = known_instance_ids(local, assignments)
    for expected in (
        "cfg-1",
        "dom-1",
        "note-1",
        "name-1",
        "exp-1",
        "cum-1",
        "reset-1",
        "order-1",
        "active-1",
        "man1",
        "manual:man1",
        "relay-host-1",
        "d1-1",
        "legacy-1",
    ):
        assert expected in ids
    assert "inst-relay" not in ids
    assert "missing" not in ids


def test_delete_failed_orphan_still_in_account_can_be_deleted_manually():
    rows = billing_review_rows(RECORDS, LIVE, {"kept"})
    hit = next(row for row in rows if row["instance_id"] == "old-1")
    assert hit["kind"] == "delete_failed"
    assert hit["still_billing"] is True
    assert hit["can_delete"] is True
    assert hit["old_id"] == "old-1"
    assert hit["new_id"] == "new-1"
    assert hit["http_status"] == 500
    assert hit["created_at"] == "2026-11-01T00:00:00Z"
    assert hit["ip"] == "1.2.3.4"
    assert hit["region"] == "nrt"
    assert hit["caution"] is False


def test_delete_failed_orphan_already_gone_is_not_deletable():
    rows = billing_review_rows(RECORDS, LIVE, {"kept"})
    hit = next(row for row in rows if row["old_id"] == "gone-1")
    assert hit["still_billing"] is False
    assert hit["can_delete"] is False
    assert hit["ip"] == ""


def test_live_instance_unknown_to_config_and_d1_is_listed_once():
    rows = billing_review_rows(RECORDS, LIVE, {"kept"})
    kinds = {row["instance_id"]: row["kind"] for row in rows}
    assert kinds["stray"] == "unregistered"
    stray = next(row for row in rows if row["instance_id"] == "stray")
    assert stray["still_billing"] is True
    assert stray["can_delete"] is True
    assert stray["created_at"] == "2026-10-02"
    assert stray["ip"] == "9.9.9.9"
    assert "kept" not in kinds
    assert [row["instance_id"] for row in rows].count("old-1") == 1


def test_replacement_from_a_failed_delete_is_shown_but_not_deletable():
    """新实例可能还没写入本地配置。要看得见，但删除按钮只给旧实例。"""
    instances = list(LIVE) + [
        {
            "id": "new-1",
            "label": "replacement",
            "main_ip": "5.5.5.5",
            "region": "nrt",
            "date_created": "2026-11-03",
        }
    ]
    rows = billing_review_rows(RECORDS, instances, {"kept"})
    new = next(row for row in rows if row["instance_id"] == "new-1")
    assert new["kind"] == "replacement"
    assert new["can_delete"] is False
    assert new["still_billing"] is True
    assert any(row["instance_id"] == "old-1" and row["can_delete"] for row in rows)


def test_switch_new_id_is_shown_but_not_deletable():
    rows = billing_review_rows([], LIVE, {"kept"}, protected_ids={"stray"})
    stray = next(row for row in rows if row["instance_id"] == "stray")
    assert stray["kind"] == "replacement"
    assert stray["can_delete"] is False
    assert any(row["instance_id"] == "ctrl" and row["can_delete"] for row in rows)


def test_missing_switch_flow_hides_unregistered_deletes():
    """切换记录没载入时，不能把可能是新实例的机器列成可删。"""
    rows = billing_review_rows(RECORDS, LIVE, {"kept"}, replacements_checked=False)
    assert [row["instance_id"] for row in rows] == ["old-1", "gone-1"]
    assert all(row["kind"] == "delete_failed" for row in rows)


def test_review_row_order_is_orphan_records_then_unknown_instances():
    rows = billing_review_rows(RECORDS, LIVE, {"kept"})
    assert [row["instance_id"] for row in rows] == ["old-1", "gone-1", "stray", "ctrl"]


def test_control_looking_instance_is_flagged_and_still_not_auto_removed():
    rows = billing_review_rows([], LIVE, {"kept", "old-1", "stray"})
    assert [row["instance_id"] for row in rows] == ["ctrl"]
    assert rows[0]["caution"] is True
    assert rows[0]["can_delete"] is True
    assert rows[0]["kind"] == "unregistered"


def test_failed_assignment_fetch_does_not_offer_unknown_instances_for_deletion():
    rows = billing_review_rows([], LIVE, set(), assignments_ok=False)
    assert rows == []


def test_unchecked_live_list_does_not_offer_delete():
    rows = billing_review_rows(RECORDS, [], set(), live_checked=False)
    assert [row["old_id"] for row in rows] == ["old-1", "gone-1"]
    assert all(row["still_billing"] is None for row in rows)
    assert all(row["can_delete"] is False for row in rows)
    assert all(row["kind"] == "delete_failed" for row in rows)


def test_review_rows_do_not_mutate_inputs():
    records = [dict(RECORDS[0])]
    instances = [dict(LIVE[0])]
    known = {"kept"}
    billing_review_rows(records, instances, known)
    assert records == [RECORDS[0]]
    assert instances == [LIVE[0]]
    assert known == {"kept"}


def test_orphan_delete_asks_first_and_does_not_switch():
    """删除必须经确认框，并且不能走到切换 IP。"""
    text = Path("app/ui/servers_page.py").read_text(encoding="utf-8")
    start = text.index("    def _delete_orphan_instance(")
    end = text.index("\n    def ", start + 10)
    body = text[start:end]
    assert "QMessageBox.question" in body
    assert "delete_instance(" in body
    assert "admin_switch" not in body
    assert "_switch_ip" not in body
    assert "_on_deleted" not in body


def test_vultr_reset_flow_call_site_uses_vultr_endpoint_and_keeps_baseline():
    """relay 的 reset_flow 对 Vultr 实例返回「分配不存在」。这里必须改走 vultr。"""
    text = Path("app/ui/users_page.py").read_text(encoding="utf-8")
    start = text.index("    def _reset_flow(")
    end = text.index("    def _load_switch_count(")
    body = text[start:end]
    assert "reset_vultr_flow(" in body
    assert "api_client.reset_flow(" not in body
    assert "server_flow_reset" in body
    assert "with_flow_reset_baseline(" in body
