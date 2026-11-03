"""激活候选不重复插入快照，以及凭据默认值不再写死在仓库里。"""

import hashlib
import re
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


# sha256 of the old hardcoded IPQS key. The key itself stays out of the repo.
_REMOVED_IPQS_KEY_SHA256 = "90d71aadedefd57b3fdc898fee7344684740ee8003ac22bfa1d16cd85b06fe21"


def test_ipqs_key_comes_only_from_config(monkeypatch):
    monkeypatch.setattr(
        "app.ipqs.config.get",
        lambda key, default=None: "from-config" if key == "ipqs_key" else default,
    )
    assert ipqs_key() == "from-config"
    monkeypatch.setattr(
        "app.ipqs.config.get",
        lambda key, default=None: "" if key == "ipqs_key" else default,
    )
    assert ipqs_key() == ""
    for rel in ("app/ipqs.py", "app/ui/webshare_page.py"):
        text = Path(rel).read_text(encoding="utf-8")
        assert "DEFAULT_KEY" not in text
        for token in re.findall(r"[A-Za-z0-9]{20,}", text):
            digest = hashlib.sha256(token.encode()).hexdigest()
            assert digest != _REMOVED_IPQS_KEY_SHA256, rel
