"""激活候选不重复插入快照，以及凭据默认值不再写死在仓库里。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import DEFAULTS  # noqa: E402
from app.ipqs import ipqs_key  # noqa: E402
from app.relay import relay_node_data  # noqa: E402


def _relay(**extra):
    base = {
        "id": "ab",
        "name": "n",
        "host": "2.2.2.2",
        "port": 200,
        "user": "b",
        "password": "pb",
        "ws_account": "acc-b",
        "relay_port": 45679,
        "relay_password": "rp",
        "entry_domain": "n.example",
        "reality_uuid": "u",
        "reality_public_key": "pub",
        "reality_short_id": "sid",
    }
    base.update(extra)
    return base


def test_relay_node_data_does_not_duplicate_when_active_is_not_first():
    relay = _relay(
        candidate_upstreams=[
            {"host": "1.1.1.1", "port": 100, "user": "a", "password": "pa"},
            {"host": "2.2.2.2", "port": 200, "user": "b", "password": "pb"},
        ],
        active_upstream_idx=1,
    )
    nd = relay_node_data(relay, "9.9.9.9", "sw")
    assert [c["host"] for c in nd["_creds"]["candidates"]] == ["1.1.1.1", "2.2.2.2"]
    assert nd["ip"] == "2.2.2.2"
    assert nd["_creds"]["upstream"]["host"] == "2.2.2.2"
    assert nd["_creds"]["active_idx"] == 1


def test_relay_node_data_prepends_host_missing_from_candidates():
    relay = _relay(
        host="9.9.9.9",
        port=900,
        user="z",
        password="pz",
        candidate_upstreams=[{"host": "1.1.1.1", "port": 100, "user": "a", "password": "pa"}],
        active_upstream_idx=0,
    )
    nd = relay_node_data(relay, "10.0.0.1", "sw")
    assert [c["host"] for c in nd["_creds"]["candidates"]] == ["9.9.9.9", "1.1.1.1"]


def test_proxy_defaults_are_empty():
    assert DEFAULTS["proxy_user"] == ""
    assert DEFAULTS["proxy_pass"] == ""


def test_ipqs_key_comes_only_from_config(monkeypatch):
    monkeypatch.setattr("app.ipqs.config.get", lambda key, default=None: "from-config" if key == "ipqs_key" else default)
    assert ipqs_key() == "from-config"
    monkeypatch.setattr("app.ipqs.config.get", lambda key, default=None: "" if key == "ipqs_key" else default)
    assert ipqs_key() == ""
    text = Path("app/ipqs.py").read_text(encoding="utf-8")
    page = Path("app/ui/webshare_page.py").read_text(encoding="utf-8")
    assert "fqjGYzhdyEYZS5YqE7KcFtUqJwXJC75W" not in text
    assert "fqjGYzhdyEYZS5YqE7KcFtUqJwXJC75W" not in page
