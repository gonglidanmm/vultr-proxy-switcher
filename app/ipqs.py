"""IPQS 检测共享逻辑（webshare-IP 页 + 中转节点页共用）。"""
from __future__ import annotations

import json
import time
import urllib.request

from . import config

DEFAULT_KEY = "fqjGYzhdyEYZS5YqE7KcFtUqJwXJC75W"


def bj_today() -> str:
    """北京时间今天的日期（UTC+8 固定偏移，与本机时区无关）。"""
    return time.strftime("%Y-%m-%d", time.gmtime(time.time() + 8 * 3600))


def ipqs_key() -> str:
    return config.get("ipqs_key") or DEFAULT_KEY


def ipqs_remaining() -> int:
    """今天剩余检测次数（日限额 35，本地计数，跨北京时间 0 点重置）。"""
    limit = int(config.get("ipqs_daily_limit", 35))
    usage = config.get("ipqs_usage", {})
    today = bj_today()
    if usage.get("date") != today:
        return limit
    return max(0, limit - int(usage.get("count", 0)))


def bump_ipqs_usage() -> None:
    """已用检测次数 +1（本地计数）。"""
    today = bj_today()
    usage = config.get("ipqs_usage", {})
    if usage.get("date") != today:
        usage = {"date": today, "count": 1}
    else:
        usage["count"] = int(usage.get("count", 0)) + 1
    config.set_value("ipqs_usage", usage)


def check_ip(ip: str) -> dict:
    """IPQS 检测单个 IP。

    返回 {"score", "proxy", "vpn", "tor", "abuse"}；
    额度耗尽返回 {"quota": True, "message": ...}；网络/key 失败抛异常。
    """
    url = f"https://www.ipqualityscore.com/api/json/ip/{ipqs_key()}/{ip}"
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=15) as resp:
        r = json.load(resp)
    if r.get("success"):
        return {
            "score": r.get("fraud_score", 0),
            "proxy": r.get("proxy"),
            "vpn": r.get("vpn"),
            "tor": r.get("tor"),
            "abuse": r.get("recent_abuse"),
        }
    return {"quota": True, "message": r.get("message", "IPQS 拒绝")}
