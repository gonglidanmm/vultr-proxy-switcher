"""Cloudflare Worker API 客户端（用户管理）。

管理端通过 HTTPS 调用 Worker，读写 D1 里的用户和分配关系。
凭据不在 config.json 里，而是读本地文件（部署时生成）：
  - ~/.vultr-proxy-switcher/worker_url.txt         Worker 地址（如 https://user.yduas.cc）
  - ~/.vultr-proxy-switcher/worker_admin_token.txt 管理员 token
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

_CONFIG_DIR = Path.home() / ".vultr-proxy-switcher"
_URL_FILE = _CONFIG_DIR / "worker_url.txt"
_TOKEN_FILE = _CONFIG_DIR / "worker_admin_token.txt"

_TIMEOUT = 20


class ApiError(Exception):
    """Worker API 错误，带可展示的中文消息。"""

    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.message = message
        self.status = status


def worker_url() -> str:
    try:
        return _URL_FILE.read_text(encoding="utf-8").strip().rstrip("/")
    except OSError:
        return ""


def _admin_token() -> str:
    try:
        return _TOKEN_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _request(method: str, path: str, body: dict | None = None, admin: bool = True, user_token: str = "") -> dict:
    base = worker_url()
    if not base:
        raise ApiError("Worker 地址未配置（缺少 worker_url.txt），请先完成部署")
    url = base + path
    headers = {"Content-Type": "application/json", "User-Agent": "vultr-proxy-switcher/1.0"}
    if admin:
        headers["X-Admin-Token"] = _admin_token()
    if user_token:
        headers["Authorization"] = f"Bearer {user_token}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, method=method, headers=headers, data=data)
    # 强制直连：绕过系统代理（Cloudflare 应直连，不依赖本地翻墙代理开关状态）
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read().decode("utf-8"))
        except Exception:
            detail = {}
        msg = detail.get("error") or f"HTTP {e.code}"
        raise ApiError(msg, e.code)
    except urllib.error.URLError as e:
        raise ApiError(f"无法连接 Worker：{e.reason}")


# ---------------- 用户 ----------------

def list_users() -> dict:
    return _request("GET", "/admin/users")


def create_user(username: str, password: str, expires_at: str | None = None, note: str = "", status: str = "active") -> dict:
    body = {"username": username, "password": password, "status": status, "note": note}
    if expires_at:
        body["expires_at"] = expires_at
    return _request("POST", "/admin/users", body)


def get_user(user_id: int) -> dict:
    return _request("GET", f"/admin/users/{user_id}")


def update_user(user_id: int, **fields) -> dict:
    return _request("PUT", f"/admin/users/{user_id}", fields)


def reset_user_switch(user_id: int, resource_id: str | None = None) -> dict:
    """重置用户切换次数。指定 resource_id 只重置该节点，否则重置该用户全部节点。"""
    body = {"reset_switch": True}
    if resource_id:
        body["resource_id"] = resource_id
    return _request("PUT", f"/admin/users/{user_id}", body)


def list_switch_flow() -> dict:
    """返回最近完成的切换记录（old_id → new_id + 销毁旧机前抓取的 old_used_gb），用于流量累加。"""
    return _request("GET", "/admin/switch-flow")


def delete_user(user_id: int) -> dict:
    return _request("DELETE", f"/admin/users/{user_id}")


# ---------------- 分配 ----------------

def assign(user_id: int, resource_type: str, resource_id: str, node_data: dict, expires_at: str | None = None, quota_gb: float | None = None) -> dict:
    body = {
        "user_id": user_id,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "node_data": node_data,
    }
    if quota_gb is not None:
        body["quota_gb"] = quota_gb
    if expires_at:
        body["expires_at"] = expires_at
    return _request("POST", "/admin/assignments", body)


def unassign(user_id: int, resource_type: str, resource_id: str) -> dict:
    return _request("DELETE", "/admin/assignments", {
        "user_id": user_id,
        "resource_type": resource_type,
        "resource_id": resource_id,
    })


def list_resource_assignments(resource_type: str, resource_id: str) -> dict:
    """查询某个资源（vultr）分配给了哪些用户（含用户名/备注/分配时间）。"""
    from urllib.parse import quote
    path = f"/admin/resource-assignments?resource_type={quote(resource_type)}&resource_id={quote(resource_id)}"
    return _request("GET", path)


def list_all_assignments() -> dict:
    """查询所有用户的分配（扁平列表，含用户名/节点快照/用户到期/分配时间）。"""
    return _request("GET", "/admin/all-assignments")


# ---------------- 切换 IP（走 Worker） ----------------


def admin_switch(resource_id: str, region: str = "", domain: str = "", creds: dict | None = None, plan: str = "") -> dict:
    """管理端切换 IP：触发 Worker 销毁重建，返回 { ok, old_id, new_id, status }。

    plan 可选指定新套餐（如 vhp-1c-1gb-amd），不传则沿用旧机套餐。
    仅服务端可用；客户端切换 IP 不能指定套餐。
    """
    body = {"resource_id": resource_id}
    if region:
        body["region"] = region
    if domain:
        body["domain"] = domain
    if creds:
        body["creds"] = creds
    if plan:
        body["plan"] = plan
    return _request("POST", "/admin/switch", body, admin=True)


def admin_switch_status() -> dict:
    """查询管理端切换状态，done 后返回 { status, new_ip, new_id, label, region }。"""
    return _request("GET", "/admin/switch/status", admin=True)


def sync_flow(updates: list[dict]) -> dict:
    """批量同步每台服务器的累计已用流量（GB）到 D1。

    updates: [{"resource_id": str, "used_gb": float}, ...]
    """
    return _request("POST", "/admin/sync-flow", {"updates": updates})


# ---------------- 统计 ----------------

def stats() -> dict:
    return _request("GET", "/admin/stats")
