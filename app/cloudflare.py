"""Cloudflare DNS 同步：切换 IP 时把域名 A 记录指到新 IP。"""

from __future__ import annotations

import requests

BASE = "https://api.cloudflare.com/client/v4"

# 强制直连，避免被系统代理（v2rayN/Shadowrocket 本地代理端口）劫持导致失败
_NO_PROXY = {"http": None, "https": None}


class CloudflareError(Exception):
    pass


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def get_zone_id(token: str, domain: str) -> str:
    """从完整域名反查所属 zone 的 id（如 jp2.yduas.cc → zone yduas.cc）。"""
    parts = domain.split(".")
    for i in range(len(parts) - 1):
        root = ".".join(parts[i:])
        r = requests.get(
            f"{BASE}/zones",
            headers=_headers(token),
            params={"name": root},
            timeout=15,
            proxies=_NO_PROXY,
        )
        if r.status_code != 200:
            raise CloudflareError(f"Cloudflare 返回 {r.status_code}")
        for z in r.json().get("result", []):
            if z.get("name") == root:
                return z["id"]
    raise CloudflareError(f"找不到域名 {domain} 所属的 zone（令牌权限不足？）")


def sync_domain(token: str, zone_id: str, domain: str, ip: str, ttl: int = 60) -> tuple[str, str]:
    """创建或更新 A 记录 domain → ip（DNS-only，不代理）。返回 (action, record_id)。"""
    headers = _headers(token)
    r = requests.get(
        f"{BASE}/zones/{zone_id}/dns_records",
        headers=headers,
        params={"name": domain, "type": "A"},
        timeout=15,
        proxies=_NO_PROXY,
    )
    if r.status_code != 200:
        raise CloudflareError(f"查询记录失败 HTTP {r.status_code}")
    recs = r.json().get("result", [])
    payload = {"type": "A", "name": domain, "content": ip, "ttl": ttl, "proxied": False}
    if recs:
        rid = recs[0]["id"]
        r2 = requests.put(
            f"{BASE}/zones/{zone_id}/dns_records/{rid}",
            headers=headers,
            json=payload,
            timeout=15,
            proxies=_NO_PROXY,
        )
        action = "updated"
    else:
        r2 = requests.post(
            f"{BASE}/zones/{zone_id}/dns_records",
            headers=headers,
            json=payload,
            timeout=15,
            proxies=_NO_PROXY,
        )
        action = "created"
    data = r2.json()
    if not data.get("success"):
        raise CloudflareError(str(data.get("errors")))
    return action, data["result"]["id"]


def test_connection(token: str, domain: str, zone_id: str = "") -> dict:
    """验证 CF 令牌 + 主域名，返回 {ok, zone_id, zone_name, message}。"""
    if not token:
        return {"ok": False, "message": "未填写 CF 令牌"}
    r = requests.get(f"{BASE}/zones", headers=_headers(token), timeout=15, proxies=_NO_PROXY)
    if r.status_code != 200:
        return {"ok": False, "message": f"令牌无效（HTTP {r.status_code}）"}
    data = r.json()
    if not data.get("success"):
        return {"ok": False, "message": f"令牌无效：{data.get('errors')}"}
    if not domain:
        return {"ok": False, "message": "未填写主域名"}
    try:
        zid = zone_id or get_zone_id(token, domain)
    except CloudflareError as e:
        return {"ok": False, "message": str(e)}
    rz = requests.get(f"{BASE}/zones/{zid}", headers=_headers(token), timeout=15, proxies=_NO_PROXY)
    if rz.status_code == 200 and rz.json().get("success"):
        zname = rz.json().get("result", {}).get("name", domain)
        return {"ok": True, "zone_id": zid, "zone_name": zname, "message": f"连接成功，主域名 zone：{zname}"}
    return {"ok": True, "zone_id": zid, "zone_name": domain, "message": "连接成功（令牌 + zone 已确认）"}


def list_dns_records(token: str, zone_id: str) -> list[dict]:
    """列出 zone 下所有 A 记录，返回 [{name, content, id}]（按 name 排序）。"""
    r = requests.get(
        f"{BASE}/zones/{zone_id}/dns_records",
        headers=_headers(token),
        params={"type": "A", "per_page": 500},
        timeout=15,
        proxies=_NO_PROXY,
    )
    if r.status_code != 200:
        raise CloudflareError(f"查询 DNS 记录失败 HTTP {r.status_code}")
    recs = []
    for rec in r.json().get("result", []):
        n = rec.get("name", "")
        if n:
            recs.append({"name": n, "content": rec.get("content", ""), "id": rec.get("id", "")})
    recs.sort(key=lambda x: x["name"])
    return recs


def delete_dns_record(token: str, zone_id: str, record_id: str) -> None:
    """删除一条 DNS 记录。"""
    r = requests.delete(
        f"{BASE}/zones/{zone_id}/dns_records/{record_id}",
        headers=_headers(token),
        timeout=15,
        proxies=_NO_PROXY,
    )
    if r.status_code != 200 or not r.json().get("success"):
        raise CloudflareError(f"删除记录失败：{r.json().get('errors')}")
