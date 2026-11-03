"""跨账号替换 IP 的核心逻辑单元测试（TDD）。

被测函数在 app/relay_ops.py，全部为纯函数，不依赖 GUI/网络。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.relay_ops import (  # noqa: E402
    account_name_for_key,
    available_pool,
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


def test_available_pool_cur_host_overrides_used():
    """当前线路自己的 IP：即使 host:port 命中 used，也允许（因为属于本线路候选）。"""
    proxies = [
        {"proxy_address": "5.5.5.5", "port": 500},
    ]
    used = {("5.5.5.5", 500)}
    recycled = set()
    cur_hosts = {"5.5.5.5"}
    out = available_pool(proxies, used, recycled, cur_hosts)
    assert len(out) == 0  # host 在 cur_hosts → 排除（替换不能选自己）


def test_available_pool_empty_input():
    assert available_pool([], set(), set(), set()) == []


# ---------- account_name_for_key ----------

def test_account_name_for_key_found():
    assert account_name_for_key("key-b", ACCOUNTS) == "baoyexiong4980"


def test_account_name_for_key_missing():
    assert account_name_for_key("nope", ACCOUNTS) == ""
