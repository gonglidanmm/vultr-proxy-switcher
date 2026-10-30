"""Webshare API 客户端：跨 plan 拉取「代理服务器(机房)」+「静态住宅(ISP)」列表。

webshare 的机房(default)和静态住宅(isp)是**不同 plan**，必须：
1. 先 `/subscription/plan/` 列出所有 active plan；
2. 对每个 plan 用 `plan_id` 分别拉 `/proxy/list/`，否则只拉到 default plan（机房）漏掉住宅。

- 静态住宅(isp)：direct 模式直连真实 IP（如 `208.66.76.75:5999`，username 与机房不同）。
- 轮换住宅(residential)：才走 `p.webshare.io` backbone 骨干（proxy_address 为空）。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

_BASE = "https://proxy.webshare.io/api/v2"
_TIMEOUT = 20

# 归到「静态住宅」的 plan 子类型（isp=静态住宅，residential=轮换住宅）
_RESIDENTIAL_SUBTYPES = {"isp", "residential"}


class WebshareError(Exception):
    """webshare API 错误，带可展示的中文消息。"""

    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.message = message
        self.status = status


def _resolve_base() -> tuple[str, dict, bool]:
    """返回 (base_url, extra_headers, use_direct)。

    优先走 Cloudflare Worker 代理（管理端免翻墙，user.yduas.cc 大陆可直连）；
    未部署 Worker（worker_url.txt 不存在）时回退直连 webshare（需走系统代理）。
    """
    try:
        from .api_client import worker_url, _admin_token

        wu = worker_url()
        if wu:
            return f"{wu}/ws-proxy", {"X-Admin-Token": _admin_token()}, True
    except Exception:
        pass
    return _BASE, {}, False


def _get(api_key: str, path: str) -> dict:
    if not api_key:
        raise WebshareError("请先填写 webshare API 密钥")
    base, extra_headers, use_direct = _resolve_base()
    url = base + path
    headers = {
        "Authorization": f"Token {api_key}",
        "User-Agent": "vultr-proxy-switcher/1.0",
        **extra_headers,
    }
    req = urllib.request.Request(url, headers=headers)
    if use_direct:
        # 走 Worker（Cloudflare 大陆可直连）：强制直连，绕过系统代理
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    else:
        # 直连 webshare：走系统代理（fake-ip 环境直连 198.18.x.x 必超时）
        opener = urllib.request.build_opener()
    try:
        with opener.open(req, timeout=_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read().decode("utf-8"))
        except Exception:
            detail = {}
        msg = detail.get("detail") or detail.get("error") or f"HTTP {e.code}"
        raise WebshareError(msg, e.code)
    except urllib.error.URLError as e:
        raise WebshareError(f"无法连接 webshare：{e.reason}")
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise WebshareError(f"webshare 返回异常数据：{e}")
    except OSError as e:
        raise WebshareError(f"连接超时或网络错误：{e}")


def list_plans(api_key: str) -> list[dict]:
    """列出所有 active plan（机房 + 静态住宅等多个 plan）。"""
    data = _get(api_key, "/subscription/plan/")
    plans = data.get("results", [])
    return [p for p in plans if p.get("status") == "active"]


def _list_proxies(api_key: str, plan_id: int, mode: str, page_size: int = 100) -> list[dict]:
    """分页拉取某个 plan 的代理列表。"""
    results: list[dict] = []
    page = 1
    while page <= 200:  # 安全上限，防死循环
        data = _get(
            api_key,
            f"/proxy/list/?mode={mode}&plan_id={plan_id}&page={page}&page_size={page_size}",
        )
        batch = data.get("results", [])
        results.extend(batch)
        if not data.get("next") or not batch:
            break
        page += 1
    return results


def get_subscription(api_key: str) -> dict:
    """返回账户订阅信息（含 start_date / end_date，多个 plan 统一订阅周期）。"""
    return _get(api_key, "/subscription/")


def get_stats(api_key: str, plan_id: int | None = None, since: str = "") -> int:
    """返回该 plan 自 since 起的累计流量（字节）。since 为空则取最近 30 天。

    webshare 的 /stats/ 是**套餐级**按小时聚合，无单 IP 粒度。
    """
    params = []
    if plan_id:
        params.append(f"plan_id={plan_id}")
    from datetime import datetime, timedelta, timezone

    if since:
        gte = since
    else:
        gte = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    params.append(f"timestamp__gte={gte}")
    path = "/stats/?" + "&".join(params)
    data = _get(api_key, path)
    if not isinstance(data, list):
        return 0
    # 只统计真实流量，过滤 is_projected（webshare 预测未来 30 天的虚高数据）
    return sum(
        int(x.get("bandwidth_total", 0))
        for x in data
        if not x.get("is_projected", False)
    )


def _gb(b: int) -> float:
    return round(b / 1e9, 2)


def load_lists(api_key: str) -> dict:
    """返回 {proxy_servers, static_residential, plans, subscription}，跨所有 active plan。

    - 每个 proxy 带 `_plan_subtype` / `_plan_id`。
    - plans 含 subtype / 流量额度 / IP 数 / 月价 / 购买时间 / 已用流量 / 剩余流量。
    - subscription 含订阅到期时间（多个 plan 统一订阅周期）。
    """
    dc: list[dict] = []
    res: list[dict] = []
    plans_info: list[dict] = []

    # 订阅周期（流量按月重置，从 start_date 起算）
    sub = {}
    try:
        sub = get_subscription(api_key)
    except WebshareError:
        sub = {}
    since = (sub.get("start_date") or "")[:10]

    for plan in list_plans(api_key):
        subtype = plan.get("proxy_subtype", "")
        # 轮换住宅(residential)走 backbone(p.webshare.io 骨干)；静态住宅(isp)/机房(default)直连真实 IP。
        mode = "backbone" if subtype == "residential" else "direct"
        proxies = _list_proxies(api_key, plan["id"], mode)
        bw_limit = plan.get("bandwidth_limit", 0) or 0
        bw_used_bytes = 0
        try:
            bw_used_bytes = get_stats(api_key, plan["id"], since)
        except WebshareError:
            pass
        bw_used = _gb(bw_used_bytes)
        plans_info.append(
            {
                "id": plan["id"],
                "subtype": subtype,
                "bandwidth_limit": bw_limit,
                "bandwidth_used": bw_used,
                "bandwidth_remaining": round(bw_limit - bw_used, 2) if bw_limit else None,
                "proxy_count": plan.get("proxy_count", 0),
                "monthly_price": plan.get("monthly_price", 0),
                "created_at": plan.get("created_at", ""),
                "proxy_replacements_total": plan.get("proxy_replacements_total", 0),
                "proxy_replacements_used": plan.get("proxy_replacements_used", 0),
                "proxy_replacements_available": plan.get("proxy_replacements_available", 0),
            }
        )
        for p in proxies:
            p["_plan_subtype"] = subtype
            p["_plan_id"] = plan["id"]
            if subtype in _RESIDENTIAL_SUBTYPES:
                res.append(p)
            else:
                dc.append(p)
    return {"proxy_servers": dc, "static_residential": res, "plans": plans_info, "subscription": sub}


def replace_proxy(api_key: str, plan_id: int, ip_address: str, country_code: str = "") -> dict:
    """替换单个代理 IP（异步 API：返回 replacement request，需轮询状态到 completed）。"""
    if not api_key:
        raise WebshareError("请先填写 webshare API 密钥")
    base, extra_headers, use_direct = _resolve_base()
    path = f"/proxy/replace/?plan_id={plan_id}" if plan_id else "/proxy/replace/"
    url = base + path
    replace_with = (
        [{"type": "country", "country_code": country_code}]
        if country_code
        else [{"type": "any", "count": 1}]
    )
    payload = {
        "to_replace": {"type": "ip_address", "ip_addresses": [ip_address]},
        "replace_with": replace_with,
        "dry_run": False,
    }
    data = json.dumps(payload).encode("utf-8")
    headers = {
        "Authorization": f"Token {api_key}",
        "User-Agent": "vultr-proxy-switcher/1.0",
        "Content-Type": "application/json",
        **extra_headers,
    }
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    opener = (
        urllib.request.build_opener(urllib.request.ProxyHandler({}))
        if use_direct
        else urllib.request.build_opener()
    )
    try:
        with opener.open(req, timeout=_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read().decode("utf-8"))
        except Exception:
            detail = {}
        msg = detail.get("detail") or detail.get("error") or f"HTTP {e.code}"
        raise WebshareError(msg, e.code)
    except urllib.error.URLError as e:
        raise WebshareError(f"无法连接 webshare：{e.reason}")
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise WebshareError(f"webshare 返回异常数据：{e}")
    except OSError as e:
        raise WebshareError(f"连接超时或网络错误：{e}")


def get_replacement_status(api_key: str, plan_id: int, replacement_id: int) -> dict:
    """查询替换请求状态（state：validating → completed / failed）。"""
    path = (
        f"/proxy/replace/{replacement_id}/?plan_id={plan_id}"
        if plan_id
        else f"/proxy/replace/{replacement_id}/"
    )
    return _get(api_key, path)


def load_subscription(api_key: str) -> dict:
    """轻量：只拉订阅信息（plans + subscription + 每 plan 流量统计），不拉代理列表。

    供 Webshare-IP 页每分钟刷新订阅流量（分配用户弹窗读它）。
    """
    sub: dict = {}
    try:
        sub = get_subscription(api_key)
    except WebshareError:
        sub = {}
    since = (sub.get("start_date") or "")[:10]
    plans_info: list[dict] = []
    for plan in list_plans(api_key):
        bw_limit = plan.get("bandwidth_limit", 0) or 0
        bw_used_bytes = 0
        try:
            bw_used_bytes = get_stats(api_key, plan["id"], since)
        except WebshareError:
            pass
        bw_used = _gb(bw_used_bytes)
        plans_info.append({
            "id": plan["id"],
            "subtype": plan.get("proxy_subtype", ""),
            "bandwidth_limit": bw_limit,
            "bandwidth_used": bw_used,
            "bandwidth_remaining": round(bw_limit - bw_used, 2) if bw_limit else None,
            "proxy_count": plan.get("proxy_count", 0),
            "monthly_price": plan.get("monthly_price", 0),
            "created_at": plan.get("created_at", ""),
            "proxy_replacements_total": plan.get("proxy_replacements_total", 0),
            "proxy_replacements_used": plan.get("proxy_replacements_used", 0),
            "proxy_replacements_available": plan.get("proxy_replacements_available", 0),
        })
    return {"plans": plans_info, "subscription": sub}


def subscription_quota_info(ip_type: str) -> dict:
    """webshare 订阅 + 套餐流量池信息（分配用户弹窗显示 + 约束用）。

    读 config.webshare_plans / webshare_subscription（webshare-IP 页加载时持久化），
    已分配流量 = D1 里该 ip_type 的 relay 分配 quota_gb 之和。
    返回 {expiry, plan_total_gb, assigned_gb, remaining_gb}；plan 未加载时全 0。
    """
    from . import api_client, config

    plans = config.get("webshare_plans", []) or []
    sub = config.get("webshare_subscription", {}) or {}
    # 订阅到期：end_date 是 UTC 完整时间戳，转北京时间精确到秒（约束「客户到期 ≤ 订阅到期」按秒比较）
    _end = sub.get("end_date") or ""
    expiry = ""
    if _end:
        try:
            from datetime import datetime, timedelta, timezone

            _dt = datetime.fromisoformat(str(_end).replace("Z", "+00:00"))
            expiry = _dt.astimezone(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            expiry = str(_end)[:10]
    plan_total = 0.0
    used = 0.0
    for p in plans:
        subtype = p.get("subtype", "")
        is_res = subtype in _RESIDENTIAL_SUBTYPES
        if (ip_type == "res") == is_res:
            plan_total = float(p.get("bandwidth_limit", 0) or 0)
            used = float(p.get("bandwidth_used", 0) or 0)
            break
    assigned = 0.0
    try:
        data = api_client.list_all_assignments()
        for a in data.get("assignments", []):
            if a.get("resource_type") != "relay":
                continue
            nd = a.get("node_data", {}) or {}
            if isinstance(nd, str):
                import json as _json

                try:
                    nd = _json.loads(nd)
                except Exception:
                    nd = {}
            if nd.get("ip_type", "dc") == ip_type:
                try:
                    assigned += float(nd.get("quota_gb") or 0)
                except (TypeError, ValueError):
                    pass
    except Exception:
        pass
    # 总剩余 = 套餐总 - 已使用（webshare 权威口径，按小时聚合）；已分配是独立参考值
    remaining = max(0.0, plan_total - used) if plan_total > 0 else 0.0
    return {"expiry": expiry, "plan_total_gb": plan_total, "used_gb": used,
            "assigned_gb": assigned, "remaining_gb": remaining}
