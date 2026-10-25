"""Webshare API 客户端：拉取「代理服务器」+「静态住宅」列表。

webshare 的两种产品在 API 里是同一批代理对象，靠 proxy_address 区分：
  - 代理服务器（机房 / Proxy Server）：走 direct 模式，proxy_address 是真实 IP。
  - 静态住宅（ISP / Static Residential）：proxy_address 为空，走 p.webshare.io 骨干。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

_BASE = "https://proxy.webshare.io/api/v2"
_TIMEOUT = 20


class WebshareError(Exception):
    """webshare API 错误，带可展示的中文消息。"""

    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.message = message
        self.status = status


def _get(api_key: str, path: str) -> dict:
    if not api_key:
        raise WebshareError("请先填写 webshare API 密钥")
    url = _BASE + path
    headers = {
        "Authorization": f"Token {api_key}",
        "User-Agent": "vultr-proxy-switcher/1.0",
    }
    req = urllib.request.Request(url, headers=headers)
    # 强制直连，绕过系统代理
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
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


def list_all_proxies(api_key: str, page_size: int = 100) -> list[dict]:
    """分页拉取全部代理。"""
    results: list[dict] = []
    page = 1
    while page <= 200:  # 安全上限，防死循环
        data = _get(api_key, f"/proxy/list/?mode=direct&page={page}&page_size={page_size}")
        batch = data.get("results", [])
        results.extend(batch)
        if not data.get("next") or not batch:
            break
        page += 1
    return results


def load_lists(api_key: str) -> dict:
    """返回 {proxy_servers: [...], static_residential: [...]}。"""
    proxies = list_all_proxies(api_key)
    dc: list[dict] = []
    res: list[dict] = []
    for p in proxies:
        if p.get("proxy_address"):
            dc.append(p)
        else:
            res.append(p)
    return {"proxy_servers": dc, "static_residential": res}
