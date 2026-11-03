"""跨账号替换 IP 的核心逻辑单元测试（TDD）。

被测函数在 app/relay_ops.py，全部为纯函数，不依赖 GUI/网络。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.relay_ops import (  # noqa: E402
    account_choices,
    account_name_for_key,
    api_key_for_choice,
    apply_active_primary,
    apply_pool_replacement,
    upstream_changed,
    available_pool,
    endpoint_from_list,
    pick_replacement,
    resolve_replace_account,
)

ACCOUNTS = [
    {"name": "gonglidanmm", "api_key": "key-a"},
    {"name": "baoyexiong4980", "api_key": "key-b"},
]


# ---------- resolve_replace_account：决定替换用哪个账号 ----------

def test_resolve_target_ws_priority():
    """当前 IP 自己记录了账号 → 优先用它。"""
    name, key = resolve_replace_account("baoyexiong4980", "1.2.3.4", "gonglidanmm", ACCOUNTS)
    assert (name, key) == ("baoyexiong4980", "key-b")


def test_resolve_host_fallback():
    """无 IP 级账号，但 host 能反查 → 用反查结果。"""
    def mock_key_for_host(host):
        return "key-a" if host == "1.2.3.4" else ""

    name, key = resolve_replace_account("", "1.2.3.4", "", ACCOUNTS, mock_key_for_host)
    assert (name, key) == ("gonglidanmm", "key-a")


def test_resolve_line_ws_fallback():
    """无 IP 级、无反查，有线路级账号 → 用线路账号。"""
    name, key = resolve_replace_account("", "9.9.9.9", "baoyexiong4980", ACCOUNTS, lambda h: "")
    assert (name, key) == ("baoyexiong4980", "key-b")


def test_resolve_unknown_account_ignored():
    """IP 级账号名在账号列表里不存在 → 跳过，落到后续 fallback。"""
    name, key = resolve_replace_account("ghost", "1.2.3.4", "gonglidanmm", ACCOUNTS)
    assert (name, key) == ("gonglidanmm", "key-a")


def test_resolve_empty():
    """全部缺失 → 返回空（由调用方 fallback 到 active 账号）。"""
    name, key = resolve_replace_account("", "9.9.9.9", "", ACCOUNTS, lambda h: "")
    assert (name, key) == ("", "")


# ---------- available_pool：过滤可用替换 IP ----------

def test_available_pool_keeps_only_free():
    proxies = [
        {"proxy_address": "1.1.1.1", "port": 100},  # 保留
        {"proxy_address": "2.2.2.2", "port": 200},  # 已中转 → 排除
        {"proxy_address": "3.3.3.3", "port": 300},  # 已回收 → 排除
        {"proxy_address": "4.4.4.4", "port": 400},  # 当前候选 → 排除
    ]
    used = {("2.2.2.2", 200)}
    recycled = {("3.3.3.3", 300)}
    cur_hosts = {"4.4.4.4"}
    out = available_pool(proxies, used, recycled, cur_hosts)
    assert [p["proxy_address"] for p in out] == ["1.1.1.1"]


def test_available_pool_excludes_current_host():
    """当前线路的 host 一律排除，即使它也在 used 里（替换不能选回自己）。"""
    proxies = [
        {"proxy_address": "5.5.5.5", "port": 500},
    ]
    used = {("5.5.5.5", 500)}
    recycled = set()
    cur_hosts = {"5.5.5.5"}
    out = available_pool(proxies, used, recycled, cur_hosts)
    assert len(out) == 0


def test_available_pool_does_not_filter_country():
    """国家过滤在界面层，不在 available_pool。"""
    proxies = [
        {"proxy_address": "1.1.1.1", "port": 100, "country_code": "US"},
        {"proxy_address": "8.8.8.8", "port": 100, "country_code": "JP"},
    ]
    out = available_pool(proxies, set(), set(), set())
    assert [p["country_code"] for p in out] == ["US", "JP"]


def test_available_pool_empty_input():
    assert available_pool([], set(), set(), set()) == []


# ---------- account_name_for_key ----------

def test_account_name_for_key_found():
    assert account_name_for_key("key-b", ACCOUNTS) == "baoyexiong4980"


def test_account_name_for_key_missing():
    assert account_name_for_key("nope", ACCOUNTS) == ""


# ---------- 管理 IP 保存：主上游跟激活候选，不跟 candidates[0] ----------

def test_apply_active_primary_uses_second_candidate():
    """激活第 2 个候选后，线路 host 与 node_data.ip / upstream 都是第 2 个。"""
    candidates = [
        {"host": "1.1.1.1", "port": 100, "user": "a", "password": "pa",
         "ws_account": "acc-a", "quota_gb": 10, "expiry": "2026-01-01 00:00:00"},
        {"host": "2.2.2.2", "port": 200, "user": "b", "password": "pb",
         "ws_account": "acc-b", "quota_gb": 5, "expiry": "2026-06-01 00:00:00"},
    ]
    relay = {"host": "1.1.1.1", "port": 100, "user": "a", "password": "pa"}
    node = {"ip": "1.1.1.1", "quota_gb": 10, "_creds": {}}
    primary = apply_active_primary(relay, node, candidates, 1)
    assert primary["host"] == "2.2.2.2"
    assert relay["host"] == "2.2.2.2"
    assert relay["port"] == 200
    assert relay["user"] == "b"
    assert relay["password"] == "pb"
    assert relay["ws_account"] == "acc-b"
    assert node["ip"] == "2.2.2.2"
    assert node["quota_gb"] == 5
    assert node["_creds"]["upstream"] == {
        "host": "2.2.2.2",
        "port": 200,
        "user": "b",
        "password": "pb",
        "ws_account": "acc-b",
    }


def test_upstream_changed_when_active_candidate_switches():
    """候选集合没变，但激活的 IP 变了，部署用的上游也变了。"""
    assert upstream_changed("1.1.1.1", 100, "2.2.2.2", 200) is True
    assert upstream_changed("1.1.1.1", 100, "1.1.1.1", "100") is False


def test_apply_active_primary_empty_candidates():
    relay = {"host": "1.1.1.1"}
    node = {"ip": "1.1.1.1"}
    assert apply_active_primary(relay, node, [], 1) is None
    assert relay["host"] == "1.1.1.1"
    assert node["ip"] == "1.1.1.1"


# ---------- 列表替换：把新 IP 写回线路和分配快照 ----------

def test_pick_replacement_uses_newest_match():
    records = [
        {"proxy": "1.1.1.1", "proxy_port": 100, "replaced_with": "9.9.9.9",
         "replaced_with_port": 900, "created_at": "2026-01-01T00:00:00"},
        {"proxy": "1.1.1.1", "proxy_port": 100, "replaced_with": "8.8.8.8",
         "replaced_with_port": 800, "created_at": "2026-02-01T00:00:00"},
        {"proxy": "3.3.3.3", "proxy_port": 300, "replaced_with": "7.7.7.7",
         "replaced_with_port": 700, "created_at": "2026-03-01T00:00:00"},
    ]
    picked = pick_replacement(records, "1.1.1.1", 100)
    assert picked["replaced_with"] == "8.8.8.8"
    assert picked["replaced_with_port"] == 800


def test_endpoint_from_list_requires_credentials():
    proxies = [
        {"proxy_address": "8.8.8.8", "port": 800, "username": "n", "password": "pn"},
        {"proxy_address": "9.9.9.9", "port": 900, "username": "", "password": ""},
    ]
    assert endpoint_from_list(proxies, "8.8.8.8", 800) == {
        "host": "8.8.8.8", "port": 800, "user": "n", "password": "pn",
    }
    assert endpoint_from_list(proxies, "9.9.9.9", 900) is None


def test_apply_pool_replacement_rewrites_active_line_and_snapshot():
    """已生成线路的当前上游被替换后，config host 与 D1 ip 都变成新 IP。"""
    relays = [{
        "id": "r1",
        "server_id": "manual:s",
        "host": "2.2.2.2",
        "port": 200,
        "user": "b",
        "password": "pb",
        "active_upstream_idx": 1,
        "candidate_upstreams": [
            {"host": "1.1.1.1", "port": 100, "user": "a", "password": "pa",
             "quota_gb": 10, "expiry": "2026-01-01", "enabled": True, "used_gb": 3,
             "ws_account": "acc-a"},
            {"host": "2.2.2.2", "port": 200, "user": "b", "password": "pb",
             "quota_gb": 5, "expiry": "2026-06-01", "enabled": False, "used_gb": 1,
             "ws_account": "acc-b"},
        ],
    }]
    assignments = [{
        "user_id": 7,
        "resource_type": "relay",
        "resource_id": "r1",
        "quota_gb": 5,
        "node_data": {
            "ip": "2.2.2.2",
            "quota_gb": 5,
            "name": "line",
            "_creds": {
                "active_idx": 1,
                "upstream": {"host": "2.2.2.2", "port": 200, "user": "b", "password": "pb"},
                "candidates": [
                    {"host": "1.1.1.1", "port": 100, "user": "a", "password": "pa",
                     "quota_gb": 10, "used_gb": 3},
                    {"host": "2.2.2.2", "port": 200, "user": "b", "password": "pb",
                     "quota_gb": 5, "used_gb": 1, "enabled": False},
                ],
            },
        },
    }]
    new = {"host": "8.8.8.8", "port": 800, "user": "n", "password": "pn"}
    out = apply_pool_replacement(relays, assignments, "2.2.2.2", 200, new)
    relay = out["relays"][0]
    assert relay["host"] == "8.8.8.8"
    assert relay["port"] == 800
    assert relay["user"] == "n"
    assert relay["password"] == "pn"
    assert relay["candidate_upstreams"][0]["host"] == "1.1.1.1"
    assert relay["candidate_upstreams"][1]["host"] == "8.8.8.8"
    assert relay["candidate_upstreams"][1]["quota_gb"] == 5
    assert relay["candidate_upstreams"][1]["used_gb"] == 1
    assert relay["candidate_upstreams"][1]["enabled"] is False
    nd = out["assignments"][0]["node_data"]
    assert nd["ip"] == "8.8.8.8"
    assert nd["quota_gb"] == 5
    assert nd["_creds"]["upstream"]["host"] == "8.8.8.8"
    assert nd["_creds"]["upstream"]["password"] == "pn"
    assert nd["_creds"]["candidates"][1]["host"] == "8.8.8.8"
    assert nd["_creds"]["candidates"][1]["used_gb"] == 1
    assert out["changed_relay_ids"] == ["r1"]
    assert out["deploy_server_ids"] == ["manual:s"]
    assert relays[0]["host"] == "2.2.2.2"


def test_apply_pool_replacement_leaves_other_candidate_primary():
    """替换非当前上游时，不改 host / node ip，也不要求重新部署。"""
    relays = [{
        "id": "r1",
        "server_id": "manual:s",
        "host": "2.2.2.2",
        "port": 200,
        "user": "b",
        "password": "pb",
        "active_upstream_idx": 1,
        "candidate_upstreams": [
            {"host": "1.1.1.1", "port": 100, "user": "a", "password": "pa", "quota_gb": 10},
            {"host": "2.2.2.2", "port": 200, "user": "b", "password": "pb", "quota_gb": 5},
        ],
    }]
    assignments = [{
        "user_id": 7,
        "resource_type": "relay",
        "resource_id": "r1",
        "node_data": {
            "ip": "2.2.2.2",
            "_creds": {
                "active_idx": 1,
                "upstream": {"host": "2.2.2.2", "port": 200, "user": "b", "password": "pb"},
                "candidates": [
                    {"host": "1.1.1.1", "port": 100, "user": "a", "password": "pa"},
                    {"host": "2.2.2.2", "port": 200, "user": "b", "password": "pb"},
                ],
            },
        },
    }]
    new = {"host": "8.8.8.8", "port": 800, "user": "n", "password": "pn"}
    out = apply_pool_replacement(relays, assignments, "1.1.1.1", 100, new)
    assert out["relays"][0]["host"] == "2.2.2.2"
    assert out["relays"][0]["candidate_upstreams"][0]["host"] == "8.8.8.8"
    assert out["assignments"][0]["node_data"]["ip"] == "2.2.2.2"
    assert out["deploy_server_ids"] == []


def test_apply_pool_replacement_refuses_incomplete_endpoint():
    relays = [{"id": "r1", "host": "1.1.1.1", "port": 100, "user": "a", "password": "pa"}]
    out = apply_pool_replacement(relays, [], "1.1.1.1", 100, {"host": "8.8.8.8", "port": 800, "user": "", "password": ""})
    assert out["relays"][0]["host"] == "1.1.1.1"
    assert out["changed_relay_ids"] == []


# ---------- 设置页账号表与管理 IP 下拉共用 webshare_accounts ----------

def test_account_choices_keeps_second_key():
    choices = account_choices([
        {"name": " gonglidanmm ", "api_key": " key-a "},
        {"name": "baoyexiong4980", "api_key": "key-b"},
        {"name": "empty", "api_key": "  "},
    ])
    assert choices == [
        {"name": "gonglidanmm", "api_key": "key-a"},
        {"name": "baoyexiong4980", "api_key": "key-b"},
    ]
    assert api_key_for_choice("baoyexiong4980", choices) == "key-b"


def test_list_accounts_and_key_for_host_see_second_key(monkeypatch):
    """设置页写入的 webshare_accounts，list_accounts 与 key_for_host 都能看到第二个 key。"""
    stored = [
        {"name": "gonglidanmm", "api_key": "key-a"},
        {"name": "baoyexiong4980", "api_key": "key-b"},
    ]

    def fake_get(key, default=None):
        if key == "webshare_accounts":
            return stored
        return default

    monkeypatch.setattr("app.config.get", fake_get)

    def fake_load(key):
        if key == "key-b":
            return {"proxy_servers": [{"proxy_address": "9.9.9.9"}], "static_residential": []}
        return {"proxy_servers": [{"proxy_address": "1.1.1.1"}], "static_residential": []}

    monkeypatch.setattr("app.webshare.load_lists", fake_load)
    from app.webshare import key_for_host, list_accounts

    assert [a["api_key"] for a in list_accounts()] == ["key-a", "key-b"]
    assert key_for_host("9.9.9.9") == "key-b"
