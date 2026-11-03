"""用户管理页：客户 CRUD + 分配视图。

列表展示用户；点「详情」看名下分配并取消；「编辑」改密码/到期/备注/状态。
Vultr-IP 页面的「分配用户」通过 _AssignDialog 选择用户并写入节点快照。
"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QEvent, QItemSelectionModel, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import api_client, config, webshare
from ..api_client import ApiError
from ..relay import norm_candidate
from .drag_table import DragTableWidget
from .persist import SizedDialog, persist_col_widths

# 全局回显：让 Vultr-IP 页面的操作也能写入用户管理页的日志区
_g_echo = None


def set_global_echo(fn) -> None:
    global _g_echo
    _g_echo = fn


def global_echo(msg: str) -> None:
    if _g_echo is not None:
        _g_echo(msg)


COLS = ["用户名", "状态", "线路数", "总流量", "到期时间", "操作"]


def _fmt_flow(v: float) -> str:
    """流量格式化：<1GB 显示 MB（精确到个位），否则保留 2 位小数。"""
    if v < 0:
        v = 0
    if v < 1:
        return f"{v * 1024:.0f}M"
    if v < 100:
        return f"{v:.2f}G"
    return f"{v:.1f}G"


def _fmt_expires(expires_at: str | None) -> str:
    if not expires_at:
        return "不过期"
    try:
        dt = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        return dt.astimezone().strftime("%Y-%m-%d")
    except Exception:
        return str(expires_at)


def _fmt_full(ts) -> str:
    """把 ISO 时间戳格式化成 YYYY-MM-DD（只保留年月日，tooltip/详情用）。"""
    if not ts:
        return "-"
    ts = str(ts)
    if "T" in ts:
        return ts[:10]
    return ts[:10]


def _is_expired(expires_at: str | None) -> bool:
    if not expires_at:
        return False
    try:
        dt = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        return dt.timestamp() < datetime.now().timestamp()
    except Exception:
        return False


def _parse_dt(text: str):
    """解析日期字符串（多种格式），返回 datetime 或 None。"""
    text = (text or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S", "%Y/%m/%d", "%Y/%m/%d %H:%M"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _fmt_countdown(expires_at: str | None) -> str:
    """到期倒计时：还剩 X天X小时 / X小时X分钟 / X分钟 / 已过期（北京时间）。"""
    if not expires_at:
        return "-"
    from datetime import timedelta, timezone
    tz = timezone(timedelta(hours=8))
    try:
        dt = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=tz)
        else:
            dt = dt.astimezone(tz)
        secs = int((dt - datetime.now(tz)).total_seconds())
    except Exception:
        return "-"
    if secs <= 0:
        return "已过期"
    d, rem = divmod(secs, 86400)
    h, rem2 = divmod(rem, 3600)
    m = rem2 // 60
    if d > 0:
        return f"还剩 {d}天{h}小时" if h else f"还剩 {d}天"
    if h > 0:
        return f"还剩 {h}小时{m}分钟" if m else f"还剩 {h}小时"
    return f"还剩 {m}分钟"


# relay 分配「使用时长」：按天/按小时/按分钟三档，默认按天
# 按天 1-30 天；按小时 1-23 小时；按分钟 5-59 分钟（5 分钟用于测试）
_UNIT_SECONDS = (86400, 3600, 60)      # 天 / 小时 / 分钟
_UNIT_RANGES = ((1, 30), (1, 23), (5, 59))
_UNIT_SUFFIXES = (" 天", " 小时", " 分钟")


def _duration_expiry(seconds: int) -> str:
    """使用时长 → 精确到秒的到期时间（北京时间，与 Worker bjToday 的 UTC+8 一致）。"""
    from datetime import timedelta, timezone

    tz = timezone(timedelta(hours=8))
    return (datetime.now(tz) + timedelta(seconds=seconds)).strftime("%Y-%m-%d %H:%M:%S")


def _per_ip_expiry(value: int, unit: str) -> str:
    """value + 单位 → 绝对到期时间；「不过期」返回空串。"""
    if unit == "不过期":
        return ""
    idx = {"按天": 0, "按小时": 1, "按分钟": 2}[unit]
    return _duration_expiry(value * _UNIT_SECONDS[idx])


def _candidate_resumable(c: dict, extra_used: float = 0.0) -> bool:
    """候选 IP 是否可恢复启用（配额未用尽 且 到期未到）。

    extra_used = 当前实例实时流量（候选为 active 时传入；封账 + 实时 = 总已用）。
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    exp = (c.get("expiry") or "").strip()
    if exp and exp < now:
        return False
    q = float(c.get("quota_gb") or 0)
    used = float(c.get("used_gb") or 0) + extra_used
    if q > 0 and used >= q:
        return False
    return True


def _candidate_renewed(c: dict) -> bool:
    """续费后是否可启用（清零重算后已用=0，只看到期是否过期）。"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    exp = (c.get("expiry") or "").strip()
    if exp and exp < now:
        return False
    return True


def _max_settable_duration(sub_expiry: str) -> str:
    """订阅到期 − 现在 = 最多可设置时长，格式化成人类可读（天/小时/分钟）。"""
    from datetime import timedelta, timezone

    tz = timezone(timedelta(hours=8))
    now = datetime.now(tz)
    se = sub_expiry if len(sub_expiry) > 10 else sub_expiry + " 23:59:59"
    try:
        max_sec = int((datetime.strptime(se, "%Y-%m-%d %H:%M:%S").replace(tzinfo=tz) - now).total_seconds())
    except Exception:
        return "未知"
    if max_sec < 0:
        return "0 分钟（订阅已到期）"
    if max_sec >= 86400:
        d, rem = divmod(max_sec, 86400)
        h = rem // 3600
        return f"{d} 天" + (f" {h} 小时" if h else "")
    if max_sec >= 3600:
        h, rem = divmod(max_sec, 3600)
        m = rem // 60
        return f"{h} 小时" + (f" {m} 分钟" if m else "")
    return f"{max_sec // 60} 分钟"


def _user_resource_expiry(user_id: int) -> str:
    """计算该用户名下分配的资源里最早的到期时间（编辑用户时的参考上限）。"""
    try:
        data = api_client.get_user(user_id)
    except ApiError:
        return ""
    earliest = None
    for a in data.get("assignments", []):
        rt = a.get("resource_type")
        rid = str(a.get("resource_id", ""))
        expiry = ""
        if rt == "vultr":
            expiry = config.get("server_expiry", {}).get(rid, "")
        else:
            continue
        if expiry:
            dt = _parse_dt(expiry)
            if dt and (earliest is None or dt < earliest):
                earliest = dt
    return earliest.strftime("%Y-%m-%d") if earliest else ""


class UsersPage(QWidget):
    """用户管理页。"""

    def __init__(self) -> None:
        super().__init__()
        self._assigns: list[dict] = []
        self._build()
        self.refresh()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        title = QLabel('<span style="font-size:20px;font-weight:700;color:#f2f2f7;">用户管理</span>')
        layout.addWidget(title)

        top = QHBoxLayout()
        top.setSpacing(10)
        self.stats_label = QLabel("")
        self.stats_label.setStyleSheet("color:#98989d;")
        top.addWidget(self.stats_label, 1)

        self.refresh_btn = QPushButton("刷新")
        self.refresh_btn.setProperty("compact", True)
        self.refresh_btn.clicked.connect(self.refresh)
        top.addWidget(self.refresh_btn)

        self.add_btn = QPushButton("＋ 新增用户")
        self.add_btn.setProperty("primary", True)
        self.add_btn.clicked.connect(self._add_user)
        top.addWidget(self.add_btn)
        layout.addLayout(top)

        self.table = DragTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(48)
        header = self.table.horizontalHeader()
        header.setStretchLastSection(True)
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        for c in range(len(COLS) - 1):
            header.setSectionResizeMode(c, QHeaderView.ResizeMode.Interactive)
        self.table.setColumnWidth(0, 110)   # 用户名
        self.table.setColumnWidth(1, 170)   # 线路名称
        self.table.setColumnWidth(2, 130)   # 出口IP
        self.table.setColumnWidth(3, 120)   # 剩余流量
        self.table.setColumnWidth(4, 110)   # 分配时间
        self.table.setColumnWidth(5, 130)   # 到期倒计时
        # 恢复上次保存的列宽（拖动后持久化）
        saved = config.get("users_table_column_widths", {})
        for c, w in saved.items():
            try:
                if int(w) < 400:
                    self.table.setColumnWidth(int(c), int(w))
            except (ValueError, IndexError):
                pass
        header.sectionResized.connect(self._on_col_resized)
        self.table.cellDoubleClicked.connect(self._on_cell_dbl)
        self.table.row_dropped.connect(self._on_row_dropped)
        QApplication.instance().installEventFilter(self)
        layout.addWidget(self.table, 1)

        self.log_box = QPlainTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setMaximumHeight(150)
        self.log_box.setPlaceholderText("操作回显：修改密码/到期/流量、禁用客户、取消分配、取消下发等操作结果会显示在这里…")
        layout.addWidget(self.log_box)
        set_global_echo(self._log)

        hint = QLabel("提示：在 Vultr-IP 页面对资源点「分配用户」，即可把节点分给某个用户。")
        hint.setStyleSheet("color:#6c6c70;font-size:12px;")
        layout.addWidget(hint)

    def _log(self, msg: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_box.appendPlainText(f"[{ts}] {msg}")

    # ---------------- 数据 ----------------

    def refresh(self) -> None:
        try:
            assigns = api_client.list_all_assignments().get("assignments", [])
            st = api_client.stats()
            self._assigns = assigns
            self._build_users(assigns)
            self.stats_label.setText(
                f"共 {st.get('total_users', 0)} 个用户 · 活跃 {st.get('active_users', 0)} · "
                f"已分配 {st.get('assigned_users', 0)} 人 · 分配项 {st.get('total_assignments', 0)}"
            )
            self._populate()
        except ApiError as e:
            self.stats_label.setText(f'<span style="color:#ff453a;">加载失败：{e.message}</span>')
            self.table.setRowCount(0)

    def _build_users(self, assigns: list[dict]) -> None:
        """按 user_id 分组：一个用户一行，名下分配收进 lines。"""
        users: dict[str, dict] = {}
        for a in assigns:
            uid = str(a.get("user_id", ""))
            if not uid:
                continue
            if uid not in users:
                users[uid] = {
                    "user_id": a.get("user_id"),
                    "username": a.get("username", ""),
                    "status": a.get("user_status", "active"),
                    "expires_at": a.get("user_expires_at"),
                    "note": a.get("user_note", ""),
                    "switch_limit": int(a.get("user_switch_limit", 5) or 5),
                    "lines": [],
                }
            users[uid]["lines"].append(a)
        self._users = list(users.values())

    def _populate(self) -> None:
        self.table.setRowCount(len(self._users))
        for row, u in enumerate(self._users):
            lines = u["lines"]
            n_lines = len(lines)
            is_active = u.get("status", "active") == "active"
            status_text = "正常" if is_active else "已禁用"
            # 总流量：所有线路合计（配额 0 = 不限，只显示已用）
            total_used = sum(float(a.get("used_gb") or 0) + float(a.get("hist_gb") or 0) for a in lines)
            total_quota = sum(float(a.get("quota_gb") or 0) for a in lines)
            if total_quota > 0:
                traffic = f"已用 {_fmt_flow(total_used)} / {_fmt_flow(total_quota)}"
            else:
                traffic = f"已用 {_fmt_flow(total_used)}"
            # 到期时间：该用户名下最早到期的线路（最早到期那台先断）
            expiries = []
            for a in lines:
                node = a.get("node_data", {}) or {}
                if isinstance(node, str):
                    import json as _json
                    try:
                        node = _json.loads(node)
                    except Exception:
                        node = {}
                creds = node.get("_creds", {}) or {}
                cands = creds.get("candidates") or []
                act = int(creds.get("active_idx", 0) or 0)
                ip_exp = ""
                if len(cands) > 1 and act < len(cands):
                    ip_exp = (cands[act].get("expiry") or "").strip()
                ce = (ip_exp if len(cands) > 1 else "") or node.get("client_expiry", "") or ""
                if ce:
                    expiries.append(ce)
            min_exp = min(expiries) if expiries else ""
            countdown = _fmt_countdown(min_exp)

            cells = [
                u.get("username", ""),   # 0 用户名
                status_text,             # 1 状态
                f"{n_lines} 条",          # 2 线路数
                traffic,                 # 3 总流量
                countdown,               # 4 到期时间
            ]
            tips = {4: _fmt_full(min_exp)}
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if col == 0:
                    item.setData(Qt.ItemDataRole.UserRole, u.get("user_id"))
                if col == 1:
                    item.setForeground(Qt.GlobalColor.lightGray if is_active else Qt.GlobalColor.red)
                else:
                    item.setForeground(Qt.GlobalColor.white)
                if col in tips:
                    item.setToolTip(tips[col])
                if col == 4 and countdown == "已过期":
                    item.setForeground(Qt.GlobalColor.red)
                self.table.setItem(row, col, item)

            self.table.setCellWidget(row, 5, self._ops(u, row))

    def _on_row_dropped(self, src_row: int, dst_row: int) -> None:
        """拖拽排序：把 self._users 里 src_row 的元素移到 dst_row 位置，持久化 + 重新渲染。"""
        if src_row < 0 or dst_row < 0 or src_row >= len(self._users) or src_row == dst_row:
            return
        u = self._users.pop(src_row)
        insert_at = dst_row if dst_row <= len(self._users) else len(self._users)
        self._users.insert(insert_at, u)
        config.set_value("user_order", [x.get("user_id") for x in self._users])
        self._populate()

    def _on_cell_dbl(self, row: int, col: int) -> None:
        """双击用户名 → 编辑该用户。"""
        if col != 0 or row < 0 or row >= len(self._users):
            return
        u = self._users[row]
        user = {
            "id": u.get("user_id"),
            "username": u.get("username", ""),
            "status": u.get("status", "active"),
            "expires_at": u.get("expires_at"),
            "note": u.get("note", ""),
        }
        self._edit_user(user)

    def _on_col_resized(self, col: int, old: int, new: int) -> None:
        """列宽拖动后持久化（跳过 stretch 操作列）。"""
        if col == len(COLS) - 1:
            return
        widths = config.get("users_table_column_widths", {})
        widths[str(col)] = new
        config.set_value("users_table_column_widths", widths)

    def eventFilter(self, obj, event) -> bool:
        """序号列选整行、其它格选该格、表格外空白取消。"""
        if event.type() == QEvent.Type.MouseButtonPress:
            if obj is self.table.viewport():
                index = self.table.indexAt(event.position().toPoint())
                if not index.isValid():
                    self.table.clearSelection()
                elif index.column() == 0:
                    self.table.clearSelection()
                    self.table.selectRow(index.row())
                else:
                    self.table.clearSelection()
                    self.table.selectionModel().select(
                        index, QItemSelectionModel.SelectionFlag.ClearAndSelect
                    )
                return True
            if not (self.table is obj or (isinstance(obj, QWidget) and self.table.isAncestorOf(obj))):
                self.table.clearSelection()
        return super().eventFilter(obj, event)

    def _assign_to_user(self, a: dict) -> dict:
        """把分配行转换成 user dict（供用户操作按钮使用）。"""
        node_limit = int(a.get("node_switch_limit", 0) or 0)
        return {
            "id": a.get("user_id"),
            "username": a.get("username", ""),
            "status": a.get("user_status", "active"),
            "expires_at": a.get("user_expires_at"),
            "note": a.get("user_note", ""),
            "switch_limit": int(a.get("user_switch_limit", 5) or 5),
            # 每台服务器独立的切换上限（assignments.switch_limit）；0/None 表示未单独设置，沿用用户级
            "node_switch_limit": node_limit,
        }

    def _ops(self, u: dict, row: int) -> QWidget:
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        detail = QPushButton("查看详情")
        detail.setProperty("compact", True)
        detail.setFixedHeight(26)
        detail.clicked.connect(lambda _=False, u=u: self._detail(u))
        lay.addWidget(detail)

        toggle = QPushButton("禁用" if u.get("status", "active") == "active" else "启用")
        toggle.setProperty("compact", True)
        toggle.setFixedHeight(26)
        toggle.clicked.connect(lambda _=False, u=u: self._toggle_status(u))
        lay.addWidget(toggle)

        delete = QPushButton("删除")
        delete.setProperty("compact", True)
        delete.setFixedHeight(26)
        delete.clicked.connect(lambda _=False, u=u: self._delete_user(u))
        lay.addWidget(delete)
        return w

    def _share_relay(self, a: dict) -> None:
        """复制节点：从 config.relays 找 relay，复用中转节点的 NodeShareDialog（多协议 + 二维码 + 复制图片）。"""
        rid = str(a.get("resource_id", ""))
        relays = config.get("relays", []) or []
        relay = next((r for r in relays if str(r.get("id", "")) == rid), None)
        if not relay:
            QMessageBox.warning(self, "提示", "该节点已不在中转列表（可能已回收），无法复制。")
            return
        node = a.get("node_data", {}) or {}
        if isinstance(node, str):
            import json as _json
            try:
                node = _json.loads(node)
            except Exception:
                node = {}
        entry = node.get("domain", "") or relay.get("entry_domain", "") or ""
        if not entry:
            QMessageBox.warning(self, "提示", "该节点没有连接域名，无法复制。")
            return
        from .nodes_page import NodeShareDialog
        NodeShareDialog(relay, entry, self).exec()

    def _unassign_assign(self, a: dict) -> None:
        node = a.get("node_data", {}) or {}
        name = node.get("name", "") or a.get("resource_id", "")
        if a.get("resource_type") == "relay":
            # relay 回收：解除分配 + 停服务 + 标记回收（进回收页冷却）
            self._recycle_relay(a, node, name)
            return
        # 查该资源有几个客户在用
        try:
            data = api_client.list_resource_assignments(a["resource_type"], a["resource_id"])
            cnt = len(data.get("assignments", []))
        except Exception:
            cnt = 1
        msg = f"确定取消「{a.get('username', '')}」对「{name}」的分配吗？"
        if cnt > 1:
            msg += f"\n\n该资源共有 {cnt} 个客户在用，取消后其余 {cnt - 1} 个客户不受影响。"
        msg += "\n\n取消后该客户的客户端将看不到此节点，正在使用的代理会立即断开。"
        ret = QMessageBox.question(self, "取消分配", msg, QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if ret != QMessageBox.StandardButton.Yes:
            return
        try:
            api_client.unassign(a["user_id"], a["resource_type"], a["resource_id"])
            self._log(f"✅ 已取消「{a.get('username', '')}」对「{name}」的分配（客户端 30 秒内自动同步）")
            self.refresh()
        except ApiError as e:
            self._log(f"❌ 取消分配失败：{e.message}")
            QMessageBox.warning(self, "取消失败", e.message)

    def _recycle_relay(self, a: dict, node: dict, name: str) -> None:
        """手动回收 relay（带确认）：删 D1 分配 + SSH 停服务 + 标记回收（进回收页冷却）。"""
        username = a.get("username", "")
        ret = QMessageBox.question(
            self, "回收 IP",
            f"确定回收「{username}」的「{name}」吗？\n\n回收后：停止该 IP 的服务、解除分配、进「回收页」冷却，不能立即分配给其他用户。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if ret != QMessageBox.StandardButton.Yes:
            return
        self._do_recycle_relay(a, node, name)
        self.refresh()

    def _do_recycle_relay(self, a: dict, node: dict, name: str) -> None:
        """回收 relay 核心（无确认，供「取消分配」「删除用户」复用）：删 D1 分配 + 停服务 + 进回收页。"""
        if isinstance(node, str):
            import json as _json
            try:
                node = _json.loads(node)
            except Exception:
                node = {}
        relay_id = str(a.get("resource_id", ""))
        username = a.get("username", "")
        # 记录流量审计（unassign 删分配前记录，避免流量数据丢失）
        used_gb = float(a.get("used_gb") or 0)
        hist_gb = float(a.get("hist_gb") or 0)
        if used_gb > 0 or hist_gb > 0:
            try:
                api_client.record_recycle_flow(
                    relay_id, username, name,
                    node.get("ip", "") or node.get("host", ""),
                    node.get("ws_account", "") or "",
                    used_gb, hist_gb,
                )
            except Exception:
                pass
        try:
            api_client.unassign(a["user_id"], "relay", relay_id)
        except ApiError as e:
            QMessageBox.warning(self, "回收失败", e.message)
            return
        creds = node.get("_creds", {}) or {}
        server_id = creds.get("server_id", "")
        server_ip = (creds.get("switch", {}) or {}).get("server_ip", "")
        stopped = True
        if server_ip and server_id:
            stopped = self._ssh_stop_relay(relay_id, server_id, server_ip)
        self._mark_recycled(relay_id, username)
        self._log(f"✅ 已回收「{username}」的「{name}」，IP 进回收页冷却")
        if not stopped:
            QMessageBox.warning(
                self, "回收警告",
                f"「{name}」已进回收页，但服务器上的 relay 服务未能确认停止，可能继续消耗流量。\n"
                f"请手动停掉 gost-relay-{relay_id} / sing-box-relay-{relay_id}（服务器 {server_ip}）。",
            )

    def _ssh_stop_relay(self, relay_id: str, server_id: str, server_ip: str) -> bool:
        """SSH 停止 relay 的 gost + sing-box 服务（回收用，幂等），并校验真的停了。返回是否成功。"""
        import subprocess

        root_pw = config.get("root_password", "")
        if server_id.startswith("manual:"):
            mid = server_id[len("manual:"):]
            for m in (config.get("manual_servers", []) or []):
                if m.get("id") == mid:
                    root_pw = m.get("password", "") or root_pw
                    break
        else:
            sc = (config.get("server_configs", {}) or {}).get(server_id, {})
            root_pw = sc.get("root_password") or root_pw
        if not root_pw or not server_ip:
            return False
        # 停服务 + 校验 is-active 是否真的 inactive（防孤儿服务偷跑流量）
        cmd = (
            f"systemctl disable --now gost-relay-{relay_id} sing-box-relay-{relay_id} 2>/dev/null; "
            f"sleep 1; "
            f"g=$(systemctl is-active gost-relay-{relay_id} 2>/dev/null); "
            f"s=$(systemctl is-active sing-box-relay-{relay_id} 2>/dev/null); "
            f"echo RESULT:$g,$s"
        )
        try:
            r = subprocess.run(
                ["sshpass", "-p", root_pw, "ssh", "-o", "StrictHostKeyChecking=no",
                 "-o", "ConnectTimeout=10", f"root@{server_ip}", cmd],
                capture_output=True, text=True, timeout=40,
            )
            out = r.stdout + r.stderr
            if "RESULT:" not in out:
                return False
            states = out.split("RESULT:")[-1].strip()
            g_state, s_state = (states.split(",") + ["", ""])[:2]
            # 两个服务都不是 active 才算停成功（inactive/failed 都算停了）
            return g_state.strip() != "active" and s_state.strip() != "active"
        except Exception:
            return False

    def _ssh_start_relay(self, relay_id: str, server_id: str, server_ip: str) -> None:
        """SSH 重启 relay 的 gost + sing-box 服务（启用用户用，幂等；失败不阻断）。"""
        import subprocess

        root_pw = config.get("root_password", "")
        if server_id.startswith("manual:"):
            mid = server_id[len("manual:"):]
            for m in (config.get("manual_servers", []) or []):
                if m.get("id") == mid:
                    root_pw = m.get("password", "") or root_pw
                    break
        else:
            sc = (config.get("server_configs", {}) or {}).get(server_id, {})
            root_pw = sc.get("root_password") or root_pw
        if not root_pw or not server_ip:
            return
        cmd = f"systemctl enable --now gost-relay-{relay_id} sing-box-relay-{relay_id} 2>/dev/null; echo DONE"
        try:
            subprocess.run(
                ["sshpass", "-p", root_pw, "ssh", "-o", "StrictHostKeyChecking=no",
                 "-o", "ConnectTimeout=10", f"root@{server_ip}", cmd],
                capture_output=True, text=True, timeout=30,
            )
        except Exception:
            pass

    def _relay_conn(self, a: dict) -> tuple[str, str, str] | None:
        """从 assignment 提取 (relay_id, server_id, server_ip)。"""
        node = a.get("node_data", {}) or {}
        if isinstance(node, str):
            import json as _json
            try:
                node = _json.loads(node)
            except Exception:
                node = {}
        creds = node.get("_creds", {}) or {}
        relay_id = str(a.get("resource_id", ""))
        server_id = creds.get("server_id", "")
        server_ip = (creds.get("switch", {}) or {}).get("server_ip", "")
        if not relay_id or not server_ip:
            return None
        return relay_id, server_id, server_ip

    def _ssh_stop_relay_line(self, a: dict) -> None:
        conn = self._relay_conn(a)
        if conn:
            self._ssh_stop_relay(*conn)

    def _ssh_start_relay_line(self, a: dict) -> None:
        conn = self._relay_conn(a)
        if conn:
            self._ssh_start_relay(*conn)

    def _mark_recycled(self, relay_id: str, username: str) -> None:
        """回收 = 取消中转：删 config.relays + 记 recycled_relays（进回收页冷却）。"""
        relays = config.get("relays", []) or []
        relay = next((r for r in relays if str(r.get("id", "")) == str(relay_id)), None)
        recycled = config.get("recycled_relays", []) or []
        if relay and not any(str(x.get("id", "")) == str(relay_id) for x in recycled):
            entry = {
                "id": str(relay_id),
                "host": relay.get("host", ""),
                "port": relay.get("port", ""),
                "name": relay.get("name", ""),
                "ip_type": relay.get("ip_type", "dc"),
                "recycled_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "recycled_from_user": username,
                "ws_account": relay.get("ws_account", ""),
            }
            cands = relay.get("candidate_upstreams") or []
            if cands:
                entry["candidate_upstreams"] = cands  # 合并线路：候选 IP 一起进冷却，避免回归误选中
            recycled.append(entry)
            config.set_value("recycled_relays", recycled)
        config.set_value("relays", [r for r in relays if str(r.get("id", "")) != str(relay_id)])

    # ---------------- 操作 ----------------

    def _add_user(self) -> None:
        dlg = _UserDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            api_client.create_user(
                dlg.username, dlg.password, dlg.expires_at, dlg.note, dlg.status,
            )
            self._log(f"✅ 已新增用户「{dlg.username}」")
            self.refresh()
        except ApiError as e:
            self._log(f"❌ 新增用户失败：{e.message}")
            QMessageBox.warning(self, "新增失败", e.message)

    def _edit_user(self, user: dict) -> None:
        # 上限优先用该用户在「服务器页」设置的到期时间（不能超过服务器到期），fallback 账户动态
        resource_expiry = _user_resource_expiry(user["id"]) or ""
        dlg = _UserDialog(self, user, resource_expiry)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        fields: dict = {}
        if dlg.password:
            fields["password"] = dlg.password
        fields["status"] = dlg.status
        fields["expires_at"] = dlg.expires_at
        fields["note"] = dlg.note
        try:
            api_client.update_user(user["id"], **fields)
            self._log(f"✅ 已更新用户「{user.get('username', '')}」" + ("（密码已修改）" if dlg.password else ""))
            self.refresh()
        except ApiError as e:
            self._log(f"❌ 编辑失败：{e.message}")
            QMessageBox.warning(self, "编辑失败", e.message)

    def _toggle_status(self, u: dict) -> None:
        new_status = "disabled" if u.get("status", "active") == "active" else "active"
        relay_lines = [a for a in u.get("lines", []) if a.get("resource_type") == "relay"]
        try:
            api_client.update_user(u["user_id"], status=new_status)
        except ApiError as e:
            self._log(f"❌ 操作失败：{e.message}")
            QMessageBox.warning(self, "操作失败", e.message)
            return
        # 停/启名下所有 relay 服务——禁用才能真正断开软路由，启用才能恢复
        if relay_lines:
            if new_status == "disabled":
                for a in relay_lines:
                    self._ssh_stop_relay_line(a)
            else:
                for a in relay_lines:
                    self._ssh_start_relay_line(a)
        label = "禁用" if new_status == "disabled" else "启用"
        action = "停用其节点" if new_status == "disabled" else "恢复其节点"
        self._log(f"✅ 已{label}用户「{u.get('username', '')}」并{action}（软路由同步失效/恢复）")
        self.refresh()

    def _delete_user(self, u: dict) -> None:
        # 名下 relay 分配（删除前回收：停服务 + 进回收页）
        relay_assigns = [a for a in u.get("lines", []) if a.get("resource_type") == "relay"]
        msg = f"确定删除用户「{u.get('username', '')}」吗？"
        if relay_assigns:
            msg += f"\n\n其名下 {len(relay_assigns)} 条中转线路会一并回收（停服务 + 进回收页冷却）。"
        msg += "\n\n此操作不可恢复。"
        ret = QMessageBox.question(self, "删除用户", msg)
        if ret != QMessageBox.StandardButton.Yes:
            return
        # 先回收名下 relay（停服务 + 进回收页），再删用户
        for a in relay_assigns:
            node = a.get("node_data", {}) or {}
            if isinstance(node, str):
                import json as _json
                try:
                    node = _json.loads(node)
                except Exception:
                    node = {}
            name = node.get("name", "") or a.get("resource_id", "")
            self._do_recycle_relay(a, node, name)
        try:
            api_client.delete_user(u["user_id"])
            self._log(f"✅ 已删除用户「{u.get('username', '')}」及其名下分配")
            self.refresh()
        except ApiError as e:
            self._log(f"❌ 删除失败：{e.message}")
            QMessageBox.warning(self, "删除失败", e.message)

    def _detail(self, u: dict) -> None:
        dlg = _UserLinesDialog(self, u)
        dlg.exec()
        self.refresh()

    def activate(self) -> None:
        self.refresh()


class _UserDialog(SizedDialog):
    _size_key = "user_dialog_size"
    _default_w = 420
    _default_h = 520
    """新增/编辑用户。编辑时密码留空 = 不修改。"""

    def __init__(self, parent=None, user: dict | None = None, resource_expiry: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle("编辑用户" if user else "新增用户")
        self.setMinimumWidth(420)
        self.username = ""
        self.password = ""
        self.note = ""
        self.status = "active"
        self.expires_at: str | None = None
        self._resource_expiry = resource_expiry

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        form = QVBoxLayout()
        form.setSpacing(8)

        uname_lbl = QLabel("用户名")
        uname_lbl.setStyleSheet("color:#98989d;")
        form.addWidget(uname_lbl)
        self.uname_edit = QLineEdit()
        if user:
            self.uname_edit.setText(user.get("username", ""))
            self.uname_edit.setReadOnly(True)
            self.uname_edit.setStyleSheet("color:#6c6c70;")
        form.addWidget(self.uname_edit)

        pwd_lbl = QLabel("密码" + ("（留空 = 不修改）" if user else "（至少 6 位）"))
        pwd_lbl.setStyleSheet("color:#98989d;")
        form.addWidget(pwd_lbl)
        self.pwd_edit = QLineEdit()
        self.pwd_edit.setEchoMode(QLineEdit.EchoMode.Password)
        form.addWidget(self.pwd_edit)

        if resource_expiry:
            exp_lbl = QLabel(f"到期时间（留空 = 不过期；不能晚于资源到期 <span style='color:#ff9f0a;'>{resource_expiry}</span>，格式 2026-12-31 23:59）")
        else:
            exp_lbl = QLabel("到期时间（留空 = 不过期，格式 2026-12-31 23:59）")
        exp_lbl.setStyleSheet("color:#98989d;")
        form.addWidget(exp_lbl)
        self.exp_edit = QLineEdit()
        if user and user.get("expires_at"):
            try:
                dt = datetime.fromisoformat(user["expires_at"].replace("Z", "+00:00"))
                self.exp_edit.setText(dt.astimezone().strftime("%Y-%m-%d"))
            except Exception:
                self.exp_edit.setText(str(user.get("expires_at", ""))[:10])
        form.addWidget(self.exp_edit)

        st_lbl = QLabel("状态")
        st_lbl.setStyleSheet("color:#98989d;")
        form.addWidget(st_lbl)
        self.status_combo = QComboBox()
        self.status_combo.addItem("启用", "active")
        self.status_combo.addItem("禁用", "disabled")
        if user and user.get("status") == "disabled":
            self.status_combo.setCurrentIndex(1)
        form.addWidget(self.status_combo)

        note_lbl = QLabel("备注")
        note_lbl.setStyleSheet("color:#98989d;")
        form.addWidget(note_lbl)
        self.note_edit = QPlainTextEdit()
        self.note_edit.setFixedHeight(70)
        if user:
            self.note_edit.setPlainText(user.get("note", ""))
        form.addWidget(self.note_edit)

        layout.addLayout(form)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self._on_ok)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _on_ok(self) -> None:
        username = self.uname_edit.text().strip()
        password = self.pwd_edit.text()
        if not username:
            QMessageBox.warning(self, "提示", "用户名不能为空")
            return
        if self.uname_edit.isReadOnly() and not password:
            # 编辑且密码留空，允许
            pass
        elif len(password) < 6:
            QMessageBox.warning(self, "提示", "密码至少 6 位")
            return

        exp_text = self.exp_edit.text().strip()
        self.expires_at = None
        if exp_text:
            exp_dt = _parse_dt(exp_text)
            if exp_dt is None:
                QMessageBox.warning(self, "提示", "到期时间格式不正确，请用 2026-12-31 23:59")
                return
            if self._resource_expiry:
                res_dt = _parse_dt(self._resource_expiry)
                if res_dt and exp_dt > res_dt:
                    QMessageBox.warning(self, "提示", f"用户到期时间不能晚于资源到期时间 {self._resource_expiry}")
                    return
            self.expires_at = exp_dt.astimezone().isoformat()

        self.username = username
        self.password = password
        self.status = self.status_combo.currentData()
        self.note = self.note_edit.toPlainText().strip()
        self.accept()


class _DurationDialog(SizedDialog):
    """设置到期时长（分/时/日），转成绝对到期时间。"""

    _size_key = "duration_dialog_size"
    _default_w = 400
    _default_h = 200

    def __init__(self, parent, current_expiry: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle("设置到期时间")
        self.expires_at = current_expiry  # 默认保留原值（取消时用）

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        tip = QLabel("设置该 IP 的到期时长（从当前时间起算）")
        tip.setStyleSheet("color:#98989d;")
        layout.addWidget(tip)

        row = QHBoxLayout()
        row.addWidget(QLabel("时长:"))
        self.unit_combo = QComboBox()
        self.unit_combo.addItems(["按天", "按小时", "按分钟"])
        row.addWidget(self.unit_combo)
        self.dur_spin = QSpinBox()
        self.dur_spin.setRange(1, 9999)
        self.dur_spin.setValue(1)
        row.addWidget(self.dur_spin)
        row.addStretch(1)
        layout.addLayout(row)

        btns = QHBoxLayout()
        no_exp = QPushButton("不过期")
        no_exp.clicked.connect(self._set_no_expiry)
        btns.addWidget(no_exp)
        btns.addStretch(1)
        ok = QPushButton("确定")
        ok.clicked.connect(self._ok)
        btns.addWidget(ok)
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        btns.addWidget(cancel)
        layout.addLayout(btns)

    def _set_no_expiry(self) -> None:
        self.expires_at = ""
        self.accept()

    def _ok(self) -> None:
        unit_idx = self.unit_combo.currentIndex()
        seconds = self.dur_spin.value() * _UNIT_SECONDS[unit_idx]
        self.expires_at = _duration_expiry(seconds)
        self.accept()


class _ManageIpsDialog(SizedDialog):
    _size_key = "manage_ips_dialog_size"
    _default_w = 800
    _default_h = 460
    """管理某条线路的候选 IP：添加/删除/禁用/设为当前/改配额/改到期。"""

    COLS = ["上游IP", "上游账号", "配额(GB)", "已用", "到期时间", "状态", "激活", "操作"]

    def __init__(self, page, u: dict, line: dict, on_saved=None) -> None:
        super().__init__(page)
        self._page = page
        self._u = u
        self._line = line
        self._on_saved = on_saved
        nd = line.get("node_data", {}) or {}
        if isinstance(nd, str):
            import json as _json
            try:
                nd = _json.loads(nd)
            except Exception:
                nd = {}
        self._creds = nd.get("_creds", {}) or {}
        self._cands = [norm_candidate(c) for c in (self._creds.get("candidates") or [])]
        self._active_idx = int(self._creds.get("active_idx", 0) or 0)
        self._name = nd.get("name", "") or line.get("resource_id", "")
        # 线路级配额/到期（分配时设置），作为候选 IP 的默认继承值
        self._line_quota = float(line.get("quota_gb") or 0)
        self._line_expiry = (nd.get("client_expiry") or "").strip()
        self._line_ws_account = nd.get("ws_account", "") or ""
        self._quota_spins: list = []
        self._expiry_btns: list = []
        self._toggle_btns: list = []
        self._set_btns: list = []
        self._workers: list = []
        self._renewed_hosts: set = set()  # 本次会话中主动续费的候选（保存时清零分配记录）

        self.setWindowTitle(f"管理 IP - {self._name}")
        self.setMinimumSize(800, 460)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        self._head = QLabel("")
        self._head.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(self._head)

        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(40)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        hdr.setStretchLastSection(False)
        persist_col_widths(self.table, "manage_ips_col_widths", [140, 90, 80, 70, 150, 60, 50, 300])
        layout.addWidget(self.table, 1)

        # 操作回显（替换/添加/删除的结果提示）
        self._status = QLabel("")
        self._status.setTextFormat(Qt.TextFormat.RichText)
        self._status.setWordWrap(True)
        layout.addWidget(self._status)

        btns = QHBoxLayout()
        add_btn = QPushButton("＋ 添加 IP")
        add_btn.setFixedHeight(32)
        add_btn.clicked.connect(self._add_ip)
        btns.addWidget(add_btn)
        btns.addStretch(1)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        box.button(QDialogButtonBox.StandardButton.Ok).setText("保存")
        box.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        box.accepted.connect(self._save)
        box.rejected.connect(self.reject)
        btns.addWidget(box)
        layout.addLayout(btns)

        self._build_rows()

    # ---------- 状态判定 ----------
    def _ip_status(self, c: dict) -> tuple[str, Qt.GlobalColor]:
        if not c.get("enabled", True):
            return "已禁用", Qt.GlobalColor.red
        exp = (c.get("expiry") or "").strip()
        if exp:
            try:
                from datetime import timedelta, timezone as _tz
                _bj = _tz(timedelta(hours=8))
                dt = datetime.fromisoformat(exp.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=_bj)  # 无时区 = 北京时间
                else:
                    dt = dt.astimezone(_bj)
                if dt < datetime.now(_bj):
                    return "已到期", Qt.GlobalColor.red
            except Exception:
                pass
        quota = float(c.get("quota_gb") or 0)
        used = float(c.get("used_gb") or 0)
        if quota > 0 and used >= quota:
            return "已用尽", Qt.GlobalColor.red
        return "正常", Qt.GlobalColor.lightGray

    # ---------- 表格渲染 ----------
    def _build_rows(self) -> None:
        self.table.setRowCount(len(self._cands))
        self._quota_spins.clear()
        self._expiry_btns.clear()
        self._toggle_btns.clear()
        self._set_btns.clear()
        for i, c in enumerate(self._cands):
            self.table.setRowHeight(i, 44)
            # IP
            it = QTableWidgetItem(c.get("host", "") or "")
            it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            it.setForeground(Qt.GlobalColor.white)
            self.table.setItem(i, 0, it)
            # 账号（IP 级 ws_account 优先，否则继承线路级）
            acct = c.get("ws_account") or self._line_ws_account or "默认账号"
            acct_it = QTableWidgetItem(acct)
            acct_it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            acct_it.setForeground(Qt.GlobalColor.white)
            acct_it.setToolTip("该 IP 所属的 webshare 上游账号")
            self.table.setItem(i, 1, acct_it)
            # 配额
            q = QSpinBox()
            q.setRange(0, 1000000)
            # 预填：IP 级独立值优先，否则继承线路级（分配时设置的）
            q.setValue(int(c.get("quota_gb") or self._line_quota))
            q.setToolTip("0 = 不限流量；默认继承线路配额")
            self.table.setCellWidget(i, 2, q)
            self._quota_spins.append(q)
            # 已用（封账 + 当前实例实时流量 = 真实总已用）
            used_total = float(c.get("used_gb") or 0)
            if i == self._active_idx:
                used_total += float(self._line.get("used_gb") or 0)
            used_it = QTableWidgetItem(f"{used_total:.2f}G")
            used_it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            used_it.setForeground(Qt.GlobalColor.white)
            self.table.setItem(i, 3, used_it)
            # 到期（倒计时按钮，点击重新设置时长）
            exp = (c.get("expiry") or self._line_expiry or "").strip()
            countdown = _fmt_countdown(exp) if exp else "不过期"
            exp_btn = QPushButton(countdown)
            exp_btn.setProperty("compact", True)
            exp_btn.setFixedHeight(30)
            exp_btn.setToolTip(f"到期时间：{exp or '不过期'}（点击重新设置）")
            exp_btn.clicked.connect(lambda _=False, idx=i: self._edit_expiry(idx))
            self.table.setCellWidget(i, 4, exp_btn)
            self._expiry_btns.append(exp_btn)
            # 状态
            st_txt, st_color = self._ip_status(c)
            st_it = QTableWidgetItem(st_txt)
            st_it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            st_it.setForeground(st_color)
            self.table.setItem(i, 5, st_it)
            # 激活
            act_it = QTableWidgetItem("●" if i == self._active_idx else "")
            act_it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            act_it.setForeground(Qt.GlobalColor.green if i == self._active_idx else Qt.GlobalColor.darkGray)
            self.table.setItem(i, 6, act_it)
            # 操作
            op_w = QWidget()
            op_lay = QHBoxLayout(op_w)
            op_lay.setContentsMargins(0, 0, 0, 0)
            op_lay.setSpacing(4)
            set_btn = QPushButton("设为当前")
            set_btn.setProperty("compact", True)
            set_btn.setFixedSize(72, 30)
            set_btn.clicked.connect(lambda _=False, idx=i: self._set_active(idx))
            op_lay.addWidget(set_btn)
            self._set_btns.append(set_btn)
            tog = QPushButton("禁用" if c.get("enabled", True) else "启用")
            tog.setProperty("compact", True)
            tog.setFixedSize(52, 30)
            tog.clicked.connect(lambda _=False, idx=i: self._toggle(idx))
            op_lay.addWidget(tog)
            self._toggle_btns.append(tog)
            renew_btn = QPushButton("续费")
            renew_btn.setProperty("compact", True)
            renew_btn.setFixedSize(52, 30)
            renew_btn.setToolTip("清零已用流量并重新设置配额/到期（不禁用也能主动续费）")
            renew_btn.clicked.connect(lambda _=False, idx=i: self._renew(idx))
            op_lay.addWidget(renew_btn)
            replace_btn = QPushButton("替换")
            replace_btn.setProperty("compact", True)
            replace_btn.setFixedSize(52, 30)
            replace_btn.setToolTip("从 webshare 未中转列表选一个 IP 替换当前 IP")
            replace_btn.clicked.connect(lambda _=False, idx=i: self._replace_ip(idx))
            op_lay.addWidget(replace_btn)
            del_btn = QPushButton("删除")
            del_btn.setProperty("compact", True)
            del_btn.setFixedSize(52, 30)
            del_btn.clicked.connect(lambda _=False, idx=i: self._remove(idx))
            op_lay.addWidget(del_btn)
            self.table.setCellWidget(i, 7, op_w)
        self._refresh_head()

    def _refresh_head(self) -> None:
        self._head.setText(
            f"线路：<b>{self._name}</b>　共 <b>{len(self._cands)}</b> 个 IP，"
            f"当前激活第 <b style='color:#30d158'>{self._active_idx + 1}</b> 个"
        )

    def _collect(self) -> None:
        """把表格里的配额写回 self._cands（到期已存在 self._cands[i]['expiry']，由倒计时按钮维护）。"""
        for i in range(len(self._cands)):
            if i < len(self._quota_spins):
                self._cands[i]["quota_gb"] = self._quota_spins[i].value()

    def _refresh_after_edit(self) -> None:
        """编辑后刷新状态列 + 激活列 + 按钮文字（增量，不重建）。"""
        for i, c in enumerate(self._cands):
            st_txt, st_color = self._ip_status(c)
            st_it = self.table.item(i, 5)
            if st_it:
                st_it.setText(st_txt)
                st_it.setForeground(st_color)
            act_it = self.table.item(i, 6)
            if act_it:
                act_it.setText("●" if i == self._active_idx else "")
                act_it.setForeground(Qt.GlobalColor.green if i == self._active_idx else Qt.GlobalColor.darkGray)
            if i < len(self._toggle_btns):
                self._toggle_btns[i].setText("禁用" if c.get("enabled", True) else "启用")
        self._refresh_head()

    # ---------- 操作 ----------
    def _set_active(self, idx: int) -> None:
        self._collect()
        self._active_idx = idx
        self._refresh_after_edit()
        host = self._cands[idx].get("host", "") if idx < len(self._cands) else ""
        self._status.setText(
            f'<span style="color:#32d74b;">✅ 已将 {host} 设为当前 IP，点「保存」生效</span>'
        )

    def _toggle(self, idx: int) -> None:
        self._collect()
        self._cands[idx]["enabled"] = not self._cands[idx].get("enabled", True)
        self._refresh_after_edit()
        c = self._cands[idx]
        state = "禁用" if not c.get("enabled", True) else "启用"
        self._status.setText(
            f'<span style="color:#32d74b;">✅ 已{state} {c.get("host", "")}，点「保存」生效</span>'
        )

    def _renew(self, idx: int) -> None:
        """续费：清零重算 + 重新设置配额/到期 + 启用（不禁用也能主动续费）。"""
        self._collect()
        c = self._cands[idx]
        q, ok = QInputDialog.getDouble(
            self, "续费配额", f"「{c.get('host', '')}」新配额（GB，0=不限）:",
            float(c.get("quota_gb") or 0), 0, 1_000_000, 2,
        )
        if not ok:
            return
        dlg = _DurationDialog(self, (c.get("expiry") or self._line_expiry or "").strip())
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        c["quota_gb"] = q
        c["expiry"] = dlg.expires_at
        c["used_gb"] = 0
        c["enabled"] = True
        self._renewed_hosts.add(c.get("host", ""))
        self._refresh_after_edit()
        self._status.setText(
            f'<span style="color:#32d74b;">✅ 已续费 {c.get("host")}：配额 {q}G、到期已重设、已用清零，点「保存」生效</span>'
        )

    def _remove(self, idx: int) -> None:
        self._collect()
        if len(self._cands) <= 1:
            QMessageBox.warning(self, "无法删除", "至少保留 1 个 IP（删除最后一个请用「回收」）。")
            return
        removed_host = self._cands[idx].get("host", "")
        del self._cands[idx]
        if self._active_idx > idx:
            self._active_idx -= 1
        elif self._active_idx >= len(self._cands):
            self._active_idx = 0
        self._build_rows()
        self._status.setText(
            f'<span style="color:#32d74b;">✅ 已删除 {removed_host}，点「保存」生效</span>'
        )

    def _replace_ip(self, idx: int) -> None:
        """替换指定索引的 IP：从同账号的 webshare 未中转列表选一个新 IP 替换它（域名不变，客户软路由无需改）。"""
        self._collect()
        # 用当前线路的上游账号（避免跨账号替换 / 当前账号无可用 IP 导致「没反应」）
        nd = self._line.get("node_data", {}) or {}
        if isinstance(nd, str):
            import json as _json
            try:
                nd = _json.loads(nd)
            except Exception:
                nd = {}
        ws_account = nd.get("ws_account", "")
        from ..relay_ops import available_pool, resolve_replace_account

        accounts = webshare.list_accounts()
        target_host = self._cands[idx].get("host", "")
        target_ws = self._cands[idx].get("ws_account", "")
        # 默认账号：当前 IP 记录 > host 反查 > 线路级 > active
        default_name, api_key = resolve_replace_account(
            target_ws, target_host, ws_account, accounts, webshare.key_for_host
        )
        if not api_key:
            api_key = webshare.get_active_key()
            default_name = ""
            for acct in accounts:
                if acct.get("api_key") == api_key:
                    default_name = acct.get("name", "")
                    break
        if not api_key:
            self._status.setText('<span style="color:#ff9f0a;">⚠️ 请先在 Webshare-IP 页填写 API 密钥</span>')
            QMessageBox.warning(self, "无法替换", "请先在 Webshare-IP 页填写 API 密钥。")
            return

        # 预计算过滤条件（pool_loader 复用；切换账号时也用它）
        relays = config.get("relays", []) or []
        used_keys = {(r.get("host"), int(r.get("port", 0) or 0)) for r in relays}
        recycled = config.get("recycled_relays", []) or []
        recycled_keys = {(r.get("host"), int(r.get("port", 0) or 0)) for r in recycled}
        cur_hosts = {c.get("host") for c in self._cands}

        def pool_loader(name: str) -> list[dict]:
            key = ""
            for acct in accounts:
                if acct.get("name") == name:
                    key = acct.get("api_key", "")
                    break
            if not key:
                return []
            try:
                lists = webshare.load_lists(key)
            except Exception:
                return []
            all_p = (lists.get("proxy_servers", []) or []) + (lists.get("static_residential", []) or [])
            return available_pool(all_p, used_keys, recycled_keys, cur_hosts)

        available = pool_loader(default_name)
        if not available:
            self._status.setText('<span style="color:#ff9f0a;">⚠️ 没有可替换的 IP（未中转且不在冷却中的）</span>')
            QMessageBox.information(self, "无可用 IP", "没有可替换的 IP（未中转且不在冷却中的）。")
            return
        self._status.setText(f'<span style="color:#30d158;">加载 {len(available)} 个可用 IP，请选择要替换的新 IP…</span>')
        dlg = _IpPickerDialog(self, available, single=True, accounts=accounts,
                              pool_loader=pool_loader, default_account=default_name)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        items = dlg.selected()
        if not items:
            self._status.setText('<span style="color:#ff9f0a;">⚠️ 未选择 IP</span>')
            QMessageBox.information(self, "未选择", "请勾选一个要替换的 IP。")
            return
        item = items[0]
        p = item["p"]
        old_host = self._cands[idx].get("host", "")
        old_port = self._cands[idx].get("port", "")
        new_host = p.get("proxy_address") or "p.webshare.io"
        new_port = int(p.get("port", 0) or 0)
        self._cands[idx]["host"] = new_host
        self._cands[idx]["port"] = new_port
        self._cands[idx]["user"] = p.get("username", "")
        self._cands[idx]["password"] = p.get("password", "")
        self._cands[idx]["quota_gb"] = item["quota_gb"]
        self._cands[idx]["expiry"] = item["expiry"]
        self._cands[idx]["used_gb"] = 0
        self._cands[idx]["enabled"] = True
        self._cands[idx]["ws_account"] = item.get("ws_account") or default_name
        self._build_rows()
        self._status.setText(
            f'<span style="color:#32d74b;">✅ 已将 {old_host}:{old_port} 替换为 '
            f'{new_host}:{new_port}（账号 {item.get("ws_account") or default_name}，配额 {item["quota_gb"]}G，点「保存」生效）</span>'
        )

    def _edit_expiry(self, idx: int) -> None:
        """点击到期倒计时 → 弹时长设置，重新设置该 IP 的到期时间。"""
        self._collect()
        cur = self._cands[idx].get("expiry", "") or ""
        dlg = _DurationDialog(self, cur)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        self._cands[idx]["expiry"] = dlg.expires_at
        # 续费重置：清零封账，重新启用（到期未过期即可）
        self._cands[idx]["used_gb"] = 0
        if _candidate_renewed(self._cands[idx]):
            self._cands[idx]["enabled"] = True
        self._build_rows()
        host = self._cands[idx].get("host", "") if idx < len(self._cands) else ""
        self._status.setText(
            f'<span style="color:#32d74b;">✅ 已设置 {host} 的到期时间，点「保存」生效</span>'
        )

    def _add_ip(self) -> None:
        self._collect()
        from ..relay_ops import available_pool

        accounts = webshare.list_accounts()
        api_key = webshare.get_active_key()
        if not api_key:
            QMessageBox.warning(self, "无法添加", "请先在 Webshare-IP 页填写 API 密钥。")
            return
        default_name = ""
        for acct in accounts:
            if acct.get("api_key") == api_key:
                default_name = acct.get("name", "")
                break
        relays = config.get("relays", []) or []
        used_keys = {(r.get("host"), int(r.get("port", 0) or 0)) for r in relays}
        recycled = config.get("recycled_relays", []) or []
        recycled_keys = {(r.get("host"), int(r.get("port", 0) or 0)) for r in recycled}
        cur_hosts = {c.get("host") for c in self._cands}

        def pool_loader(name: str) -> list[dict]:
            key = ""
            for acct in accounts:
                if acct.get("name") == name:
                    key = acct.get("api_key", "")
                    break
            if not key:
                return []
            try:
                lists = webshare.load_lists(key)
            except Exception:
                return []
            all_p = (lists.get("proxy_servers", []) or []) + (lists.get("static_residential", []) or [])
            return available_pool(all_p, used_keys, recycled_keys, cur_hosts)

        available = pool_loader(default_name)
        if not available:
            QMessageBox.information(self, "无可用 IP", "没有可添加的 IP（未中转且不在冷却中的）。")
            return
        dlg = _IpPickerDialog(self, available, accounts=accounts,
                              pool_loader=pool_loader, default_account=default_name)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        for item in dlg.selected():
            p = item["p"]
            self._cands.append({
                "host": p.get("proxy_address") or "p.webshare.io",
                "port": int(p.get("port", 0) or 0),
                "user": p.get("username", ""),
                "password": p.get("password", ""),
                "quota_gb": item["quota_gb"],
                "used_gb": 0,
                "expiry": item["expiry"],
                "enabled": True,
                "ws_account": item.get("ws_account") or default_name,
            })
        self._build_rows()
        self._status.setText(
            f'<span style="color:#32d74b;">✅ 已添加 {len(dlg.selected())} 个 IP，点「保存」生效</span>'
        )

    # ---------- 保存 ----------
    def _save(self) -> None:
        self._collect()
        rid = str(self._line.get("resource_id", ""))
        # 续费重置：保存前是「禁用」的候选（用尽/到期），保存时清零重算 + 重新启用
        # （不管用户是改配额、改到期还是点启用，只要保存前是禁用，就当作续费清零）
        old_disabled_hosts = {
            c.get("host") for c in (self._creds.get("candidates") or [])
            if c.get("enabled") is False
        }
        resumed_any = False
        for _i, c in enumerate(self._cands):
            # 保存前是禁用，或本次会话主动点了「续费」→ 清零重算 + 重新启用
            if c.get("host") in old_disabled_hosts or c.get("host") in self._renewed_hosts:
                c["used_gb"] = 0  # 续费重置：封账流量清零
                if _candidate_renewed(c):  # 到期未过期即可启用
                    c["enabled"] = True
                    resumed_any = True
        if resumed_any:
            try:
                api_client.reset_flow(rid)  # 清零分配记录 used_gb + 切换历史
            except Exception:
                pass
        # 记录保存前的 IP 集合（判断是否需重新部署：只有 IP 变了才部署）
        old_keys = {f"{c.get('host')}:{c.get('port')}" for c in (self._creds.get("candidates") or [])}
        # 1. 更新 config.relays 的 candidate_upstreams（源）
        relays = config.get("relays", []) or []
        for r in relays:
            if str(r.get("id", "")) == rid:
                r["candidate_upstreams"] = self._cands
                r["active_upstream_idx"] = self._active_idx
                # 同步主 IP = candidates[0]（替换/删除后 host 与候选保持一致，避免「外面还是旧 IP」）
                if self._cands:
                    c0 = self._cands[0]
                    r["host"] = c0.get("host", "")
                    r["port"] = c0.get("port", 0)
                    r["user"] = c0.get("user", "")
                    r["password"] = c0.get("password", "")
                    if c0.get("ws_account"):
                        r["ws_account"] = c0.get("ws_account", "")
                break
        config.set_value("relays", relays)
        # 2. 更新 D1 node_data（快照）
        nd = self._line.get("node_data", {}) or {}
        if isinstance(nd, str):
            import json as _json
            try:
                nd = _json.loads(nd)
            except Exception:
                nd = {}
        creds = nd.get("_creds", {}) or {}
        creds["candidates"] = self._cands
        creds["active_idx"] = self._active_idx
        # 同步主 IP 快照（node_data.ip / ws_account / upstream / quota），客户端「当前 IP/配额」跟替换后一致
        if self._cands:
            c0 = self._cands[0]
            nd["ip"] = c0.get("host", "")
            if c0.get("ws_account"):
                nd["ws_account"] = c0.get("ws_account", "")
            nd["quota_gb"] = float(c0.get("quota_gb") or 0)
            creds["upstream"] = {
                "host": c0.get("host", ""),
                "port": c0.get("port", 0),
                "user": c0.get("user", ""),
                "password": c0.get("password", ""),
                "ws_account": c0.get("ws_account", ""),
            }
        nd["_creds"] = creds
        # 当前激活候选的配额/到期 → 同步到分配记录 quota_gb 与 node_data.client_expiry（外面线路明细/客户端读这两个权威值）
        active_quota = 0.0
        active_expiry = None
        if self._cands and self._active_idx < len(self._cands):
            active_quota = float(self._cands[self._active_idx].get("quota_gb") or 0)
            active_expiry = (self._cands[self._active_idx].get("expiry") or "").strip() or None
        nd["expiry"] = active_expiry or ""
        nd["client_expiry"] = active_expiry or ""  # 独立线路的到期权威字段（外面/客户端读它）
        try:
            api_client.assign(
                self._u["user_id"], "relay", rid, nd,
                quota_gb=active_quota,
            )
        except ApiError as e:
            QMessageBox.warning(self, "保存失败", e.message)
            return
        # 重新部署条件：IP 变化 或 候选从禁用恢复（配额/到期改了，之前可能被 /stop 停服务）
        new_keys = {f"{c.get('host')}:{c.get('port')}" for c in self._cands}
        old_disabled = {c.get("host") for c in (self._creds.get("candidates") or []) if c.get("enabled") is False}
        resumed = any(c.get("host") in old_disabled and c.get("enabled", True) for c in self._cands)
        if new_keys != old_keys or resumed:
            self._deploy_after_save(rid)
        else:
            self._status.setText('<span style="color:#32d74b;">✅ 已保存（IP 未变，无需重新部署）</span>')

    def _deploy_after_save(self, rid: str) -> None:
        """保存后重新部署该 relay 的服务器（替换/添加 IP 后服务器需用新 upstream）。"""
        import subprocess
        from pathlib import Path

        from .workers import CallWorker

        relays = config.get("relays", []) or []
        relay = next((r for r in relays if str(r.get("id", "")) == str(rid)), None)
        if not relay:
            self._status.setText('<span style="color:#ff9f0a;">⚠️ 已保存，但找不到该线路，请手动点「重新部署」</span>')
            return
        sid = relay.get("server_id", "")
        srv_ip = ""
        if sid.startswith("manual:"):
            mid = sid[len("manual:"):]
            for m in (config.get("manual_servers", []) or []):
                if m.get("id") == mid:
                    srv_ip = m.get("host", "") or ""
                    break
        if not srv_ip:
            self._status.setText('<span style="color:#ff9f0a;">⚠️ 已保存，但找不到服务器 IP，请到 Webshare-IP 页点「重新部署」</span>')
            return
        root = Path(__file__).resolve().parent.parent.parent
        script = root / "scripts" / "apply_relays.py"
        self._status.setText('<span style="color:#30d158;">已保存，正在重新部署到服务器…</span>')

        def _do():
            r = subprocess.run(
                ["uv", "run", "python", str(script), sid, srv_ip],
                capture_output=True, text=True, timeout=900, cwd=str(root),
            )
            return r.stdout + r.stderr

        w = CallWorker(_do)
        w.done.connect(self._on_deployed)
        w.failed.connect(lambda m: self._on_deployed(f"失败：{m}"))
        self._workers.append(w)
        w.finished.connect(w.deleteLater)
        w.start()

    def _on_deployed(self, text: str) -> None:
        lines = str(text).strip().splitlines()
        last = lines[-1] if lines else str(text)
        ok = "DONE" in str(text)
        color = "#32d74b" if ok else "#ff9f0a"
        self._status.setText(f'<span style="color:{color};">{"✅ 部署完成" if ok else "⚠️ 部署未完全成功"}：{last}</span>')
        if hasattr(self._page, "refresh"):
            try:
                self._page.refresh()
            except Exception:
                pass
        if self._on_saved:
            try:
                self._on_saved()
            except Exception:
                pass


class _IpPickerDialog(SizedDialog):
    _size_key = "ip_picker_dialog_size"
    _default_w = 820
    _default_h = 480
    """从 webshare IP 列表多选要添加的候选 IP，每个 IP 独立设置配额 + 使用时长（分/时/日）。"""

    def __init__(self, parent, available: list[dict], single: bool = False,
                 accounts: list[dict] | None = None, pool_loader=None, default_account: str = "") -> None:
        super().__init__(parent)
        self._single = single
        self._accounts = accounts or []
        self._pool_loader = pool_loader
        self.setWindowTitle("选择要替换的 IP" if single else "选择要添加的 IP")
        self.setMinimumSize(820, 480)
        self._available = available
        self._checks: list = []
        self._quota_spins: list = []
        self._unit_combos: list = []
        self._dur_spins: list = []

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # 上游账号下拉框（跨账号选择：替换/添加时可切到别的账号池）
        self.account_combo: QComboBox | None = None
        if len(self._accounts) > 1:
            acct_row = QHBoxLayout()
            acct_row.addWidget(QLabel("上游账号:"))
            self.account_combo = QComboBox()
            for a in self._accounts:
                self.account_combo.addItem(a.get("name", ""))
            if default_account:
                _i = self.account_combo.findText(default_account)
                if _i >= 0:
                    self.account_combo.setCurrentIndex(_i)
            self.account_combo.currentIndexChanged.connect(self._on_account_changed)
            acct_row.addWidget(self.account_combo)
            acct_row.addStretch(1)
            layout.addLayout(acct_row)

        self._tip = QLabel("")
        layout.addWidget(self._tip)
        self._update_tip()

        # 统一设置区
        uni = QHBoxLayout()
        uni.addWidget(QLabel("统一配额(GB,0=不限):"))
        self.uni_quota = QSpinBox()
        self.uni_quota.setRange(0, 1000000)
        self.uni_quota.setValue(10)  # 默认 10GB
        self.uni_quota.valueChanged.connect(lambda v: [s.setValue(v) for s in self._quota_spins])
        uni.addWidget(self.uni_quota)
        uni.addWidget(QLabel("统一时长:"))
        self.uni_unit = QComboBox()
        self.uni_unit.addItems(["按天", "按小时", "按分钟"])
        self.uni_unit.currentIndexChanged.connect(
            lambda _: [u.setCurrentText(self.uni_unit.currentText()) for u in self._unit_combos]
        )
        uni.addWidget(self.uni_unit)
        self.uni_dur = QSpinBox()
        self.uni_dur.valueChanged.connect(lambda v: [d.setValue(v) for d in self._dur_spins])
        self.uni_dur.setRange(1, 9999)
        self.uni_dur.setValue(1)
        uni.addWidget(self.uni_dur)
        apply_btn = QPushButton("应用到勾选")
        apply_btn.clicked.connect(self._apply_all)
        uni.addWidget(apply_btn)
        uni.addStretch(1)
        layout.addLayout(uni)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["", "IP", "配额(GB)", "时长单位", "时长"])
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(40)  # 行高，控件完整显示
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        hdr.setStretchLastSection(False)
        persist_col_widths(self.table, "ip_picker_col_widths", [40, 300, 90, 90, 70])
        layout.addWidget(self.table, 1)

        self._suppress_picker = False
        self._fill_table(available)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.button(QDialogButtonBox.StandardButton.Ok).setText("替换" if single else "添加")
        btns.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)
        if single:
            self.table.itemChanged.connect(self._on_item_changed_single)

    def _update_tip(self) -> None:
        self._tip.setText(f"勾选要添加的 IP，并为每个 IP 设置配额和到期时长（共 {len(self._available)} 个可用）")

    def _current_account(self) -> str:
        if self.account_combo:
            return self.account_combo.currentText()
        return (self._accounts[0].get("name", "") if self._accounts else "")

    def _on_account_changed(self, _idx: int) -> None:
        """切换上游账号 → 重新加载该账号的 IP 池。"""
        if self._pool_loader:
            proxies = self._pool_loader(self._current_account()) or []
            self._fill_table(proxies)

    def _fill_table(self, proxies: list[dict]) -> None:
        """用给定 IP 池重建表格（切换账号时复用）。"""
        self._suppress_picker = True
        self._available = proxies
        self._checks.clear()
        self._quota_spins.clear()
        self._unit_combos.clear()
        self._dur_spins.clear()
        self.table.setRowCount(0)
        self.table.setRowCount(len(proxies))
        for i, p in enumerate(proxies):
            self.table.setRowHeight(i, 40)
            host = p.get("proxy_address") or "p.webshare.io"
            country = p.get("country_code", "")
            city = p.get("city_name", "")
            chk = QTableWidgetItem()
            chk.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            chk.setCheckState(Qt.CheckState.Unchecked)
            self.table.setItem(i, 0, chk)
            self._checks.append(chk)
            label = f"{host}:{p.get('port', '')}  ({country} {city})"
            it = QTableWidgetItem(label)
            it.setForeground(Qt.GlobalColor.white)
            self.table.setItem(i, 1, it)
            q = QSpinBox()
            q.setRange(0, 1000000)
            q.setValue(self.uni_quota.value() if hasattr(self, "uni_quota") else 10)
            q.setToolTip("0 = 不限流量")
            self.table.setCellWidget(i, 2, q)
            self._quota_spins.append(q)
            u = QComboBox()
            u.addItems(["按天", "按小时", "按分钟"])
            if hasattr(self, "uni_unit"):
                u.setCurrentText(self.uni_unit.currentText())
            self.table.setCellWidget(i, 3, u)
            self._unit_combos.append(u)
            d = QSpinBox()
            d.setRange(1, 9999)
            d.setValue(self.uni_dur.value() if hasattr(self, "uni_dur") else 1)
            self.table.setCellWidget(i, 4, d)
            self._dur_spins.append(d)
        self._suppress_picker = False
        self._update_tip()

    def _on_item_changed_single(self, item) -> None:
        """单选模式：勾选一个后自动取消其他勾选。"""
        if getattr(self, "_suppress_picker", False):
            return
        if item.column() == 0 and item.checkState() == Qt.CheckState.Checked:
            for i in range(self.table.rowCount()):
                if i != item.row():
                    chk = self._checks[i]
                    if chk.checkState() == Qt.CheckState.Checked:
                        chk.setCheckState(Qt.CheckState.Unchecked)

    def _apply_all(self) -> None:
        q = self.uni_quota.value()
        u_idx = self.uni_unit.currentIndex()
        d = self.uni_dur.value()
        for i in range(len(self._checks)):
            if self._checks[i].checkState() == Qt.CheckState.Checked:
                self._quota_spins[i].setValue(q)
                self._unit_combos[i].setCurrentIndex(u_idx)
                self._dur_spins[i].setValue(d)

    def selected(self) -> list[dict]:
        out = []
        acct = self._current_account()
        for i in range(self.table.rowCount()):
            chk = self._checks[i]
            if chk.checkState() != Qt.CheckState.Checked:
                continue
            p = self._available[i]
            quota = self._quota_spins[i].value()
            unit_idx = self._unit_combos[i].currentIndex()
            dur = self._dur_spins[i].value()
            seconds = dur * _UNIT_SECONDS[unit_idx]
            expiry = _duration_expiry(seconds)
            out.append({"p": p, "quota_gb": quota, "expiry": expiry, "ws_account": acct})
        return out


class _UserLinesDialog(SizedDialog):
    _size_key = "user_lines_dialog_size"
    _default_w = 860
    _default_h = 620
    """用户线路明细弹窗：列出该用户名下所有线路，每条线路可复制节点/回收。"""

    def __init__(self, page, u: dict) -> None:
        super().__init__(page)
        self._page = page
        self._u = u or {}
        lines = self._u.get("lines", [])
        status = self._u.get("status", "active")
        self.setWindowTitle(f"「{self._u.get('username', '')}」的线路明细")
        self.setMinimumSize(760, 520)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        head = QLabel(
            f"用户名：<b>{self._u.get('username', '')}</b>　"
            f"状态：<b style='color:{'#30d158' if status == 'active' else '#ff453a'}'>{'正常' if status == 'active' else '已禁用'}</b>　"
            f"线路数：<b>{len(lines)}</b> 条"
        )
        head.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(head)

        table = QTableWidget(len(lines), 8)
        table.setHorizontalHeaderLabels(["线路名称", "类型", "出口IP", "上游账号", "流量", "分配时间", "到期", "操作"])
        _hdr = table.horizontalHeader()
        _hdr.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        _hdr.setStretchLastSection(False)
        # 恢复上次调整的列宽（否则用默认）
        _saved_w = config.get("user_lines_col_widths")
        if _saved_w and len(_saved_w) == 8:
            for c, w in enumerate(_saved_w):
                table.setColumnWidth(c, int(w))
        else:
            table.setColumnWidth(0, 140)  # 线路名称
            table.setColumnWidth(1, 90)   # 类型（独立/合并 N 个）
            table.setColumnWidth(2, 120)  # 出口IP
            table.setColumnWidth(3, 100)  # 上游账号
            table.setColumnWidth(4, 110)  # 流量
            table.setColumnWidth(5, 100)  # 分配时间
            table.setColumnWidth(6, 110)  # 到期
            table.setColumnWidth(7, 220)  # 操作（管理IP + 复制节点 + 回收）
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(40)  # 行高，按钮完整放下
        table.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked)
        table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        table.itemChanged.connect(self._on_line_name_changed)
        self._suppress = False
        self._table = table
        _hdr.sectionResized.connect(self._save_col_widths)  # 拖列宽实时持久化
        layout.addWidget(table, 1)

        self._suppress = True
        for row, a in enumerate(lines):
            self._render_line(table, row, a)
        self._suppress = False

        btns = QDialogButtonBox()
        edit = btns.addButton("编辑用户", QDialogButtonBox.ButtonRole.ActionRole)
        edit.clicked.connect(self._edit_user)
        close_btn = btns.addButton(QDialogButtonBox.StandardButton.Close)
        close_btn.clicked.connect(self.accept)
        layout.addWidget(btns)

    def _render_line(self, table, row: int, a: dict) -> None:
        """渲染线路明细的一行（__init__ 与 _reload 复用）。"""
        table.setRowHeight(row, 44)  # 显式行高，按钮垂直完整
        node = a.get("node_data", {}) or {}
        if isinstance(node, str):
            import json as _json
            try:
                node = _json.loads(node)
            except Exception:
                node = {}
        name = node.get("name", "") or node.get("host", "") or a.get("resource_id", "")
        creds = node.get("_creds", {}) or {}
        n_cands = len(creds.get("candidates") or [])
        type_text = f"合并 {n_cands} 个" if n_cands > 1 else "独立"
        exit_ip = node.get("ip", "") or node.get("host", "")
        quota = float(a.get("quota_gb") or 0)
        used = float(a.get("used_gb") or 0) + float(a.get("hist_gb") or 0)
        if quota > 0:
            traffic = f"已用 {_fmt_flow(used)} / {_fmt_flow(quota)}"
        else:
            traffic = f"已用 {_fmt_flow(used)}"
        full_assign = a.get("created_at", "") or ""
        try:
            adt = datetime.fromisoformat(str(full_assign).replace("Z", "+00:00"))
            if adt.tzinfo is not None:
                adt = adt.astimezone()
            assign_time = adt.strftime("%m-%d %H:%M")
        except Exception:
            assign_time = "-"
        cands2 = creds.get("candidates") or []
        act2 = int(creds.get("active_idx", 0) or 0)
        ip_exp2 = ""
        if len(cands2) > 1 and act2 < len(cands2):
            ip_exp2 = (cands2[act2].get("expiry") or "").strip()
        ce = (ip_exp2 if len(cands2) > 1 else "") or node.get("client_expiry", "") or ""
        countdown = _fmt_countdown(ce)

        for col, text in zip([1, 2, 5], [type_text, exit_ip, assign_time]):
            item = QTableWidgetItem(text)
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item.setForeground(Qt.GlobalColor.white)
            table.setItem(row, col, item)

        name_item = QTableWidgetItem(name)
        name_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        name_item.setForeground(Qt.GlobalColor.white)
        name_item.setData(Qt.ItemDataRole.UserRole, str(a.get("resource_id", "")))
        name_item.setFlags(name_item.flags() | Qt.ItemFlag.ItemIsEditable)
        name_item.setToolTip("双击编辑名称（复制节点/二维码/协议链接跟随）")
        table.setItem(row, 0, name_item)

        cands_acct = (node.get("_creds", {}) or {}).get("candidates") or []
        acct_set = {c.get("ws_account") for c in cands_acct if c.get("ws_account")}
        if len(acct_set) > 1:
            acct_text = f"多账号({len(acct_set)})"
            acct_tip = "、".join(sorted(acct_set))
        else:
            acct_text = (next(iter(acct_set)) if acct_set else "") or node.get("ws_account") or webshare.default_account_name()
            acct_tip = acct_text
        acct = QTableWidgetItem(acct_text)
        acct.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        acct.setForeground(Qt.GlobalColor.white)
        acct.setToolTip(f"上游账号：{acct_tip}")
        table.setItem(row, 3, acct)

        # 流量列(4)：只读展示（修改统一在「管理 IP」里）
        flow_it = QTableWidgetItem(traffic)
        flow_it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        flow_it.setForeground(Qt.GlobalColor.white)
        flow_it.setToolTip("配额/流量请在「管理 IP」里修改")
        table.setItem(row, 4, flow_it)

        # 到期列(6)：只读展示（修改统一在「管理 IP」里）
        exp_it = QTableWidgetItem(countdown)
        exp_it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        if "已过期" in countdown or "已到期" in countdown:
            exp_it.setForeground(Qt.GlobalColor.red)
        else:
            exp_it.setForeground(Qt.GlobalColor.white)
        exp_it.setToolTip("到期时间请在「管理 IP」里修改")
        table.setItem(row, 6, exp_it)

        op_w = QWidget()
        op_lay = QHBoxLayout(op_w)
        op_lay.setContentsMargins(0, 0, 0, 0)
        op_lay.setSpacing(6)
        manage = QPushButton("管理IP")
        manage.setProperty("compact", True)
        manage.setFixedSize(64, 32)
        manage.clicked.connect(lambda _=False, a=a: self._manage_ips(a))
        op_lay.addWidget(manage)
        share = QPushButton("复制节点")
        share.setProperty("compact", True)
        share.setFixedSize(84, 32)
        share.clicked.connect(lambda _=False, a=a: self._page._share_relay(a))
        op_lay.addWidget(share)
        recycle = QPushButton("回收")
        recycle.setProperty("compact", True)
        recycle.setFixedSize(56, 32)
        recycle.clicked.connect(lambda _=False, a=a, node=node, name=name: self._recycle(a, node, name))
        op_lay.addWidget(recycle)
        table.setCellWidget(row, 7, op_w)

    def _reload(self) -> None:
        """保存/替换后重新加载线路数据并重建表格（修复「外面还是旧 IP」）。"""
        try:
            assigns = api_client.list_all_assignments().get("assignments", [])
        except Exception:
            return
        uid = str(self._u.get("user_id", ""))
        self._u["lines"] = [x for x in assigns if str(x.get("user_id", "")) == uid]
        lines = self._u.get("lines", [])
        self._suppress = True
        self._table.setRowCount(0)
        self._table.setRowCount(len(lines))
        for row, a in enumerate(lines):
            self._render_line(self._table, row, a)
        self._suppress = False

    def _on_line_name_changed(self, item: QTableWidgetItem) -> None:
        """线路名称双击编辑后：同步 config.relays + D1 node_data.name（复制节点/二维码跟随）。"""
        if getattr(self, "_suppress", False):
            return
        if item.column() != 0:
            return
        rid = str(item.data(Qt.ItemDataRole.UserRole) or "")
        text = item.text().strip()
        if not rid or not text:
            return
        relays = config.get("relays", []) or []
        for r in relays:
            if str(r.get("id", "")) == rid:
                r["name"] = text
                break
        config.set_value("relays", relays)
        try:
            api_client.rename_assignment(rid, text)
        except Exception:
            pass

    def _edit_user(self) -> None:
        user = {
            "id": self._u.get("user_id"),
            "username": self._u.get("username", ""),
            "status": self._u.get("status", "active"),
            "expires_at": self._u.get("expires_at"),
            "note": self._u.get("note", ""),
        }
        self._page._edit_user(user)
        self.accept()

    def _recycle(self, a: dict, node: dict, name: str) -> None:
        self._page._recycle_relay(a, node, name)
        self.accept()

    def _edit_expiry(self, a: dict, node: dict) -> None:
        """点击到期列：设置该线路的到期时长（独立=线路级，合并=当前激活IP）。"""
        rid = str(a.get("resource_id", ""))
        nd = dict(node) if isinstance(node, dict) else {}
        creds = nd.get("_creds", {}) or {}
        cands = creds.get("candidates") or []
        act = int(creds.get("active_idx", 0) or 0)
        cur = ""
        if cands and act < len(cands):
            cur = (cands[act].get("expiry") or "").strip()
        cur = cur or (nd.get("client_expiry") or "")
        dlg = _DurationDialog(self, current_expiry=cur)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        new_exp = dlg.expires_at
        nd["client_expiry"] = new_exp
        if cands and act < len(cands):
            cands[act]["expiry"] = new_exp
            # 续费重置：清零封账 + 分配流量，重新启用（到期未过期即可）
            cands[act]["used_gb"] = 0
            try:
                api_client.reset_flow(rid)
            except Exception:
                pass
            if _candidate_renewed(cands[act]):
                cands[act]["enabled"] = True
            creds["candidates"] = cands
            nd["_creds"] = creds
        relays = config.get("relays", []) or []
        for r in relays:
            if str(r.get("id", "")) == rid:
                cu = r.get("candidate_upstreams") or []
                if cu and act < len(cu):
                    cu[act]["expiry"] = new_exp
                    cu[act]["used_gb"] = 0
                    if _candidate_renewed(cu[act]):
                        cu[act]["enabled"] = True
                    r["candidate_upstreams"] = cu
                break
        config.set_value("relays", relays)
        try:
            api_client.assign(a.get("user_id"), "relay", rid, nd)
        except ApiError as e:
            QMessageBox.warning(self, "设置失败", e.message)
            return
        # 之前可能因到期被停服务，重新部署恢复代理
        self._redeploy_relay(rid)
        self._page.refresh()
        self.accept()

    def _edit_quota(self, a: dict, node: dict) -> None:
        """点击流量列：设置该线路的流量配额（GB，独立=线路级，合并=当前激活IP）。"""
        rid = str(a.get("resource_id", ""))
        nd = dict(node) if isinstance(node, dict) else {}
        creds = nd.get("_creds", {}) or {}
        cands = creds.get("candidates") or []
        act = int(creds.get("active_idx", 0) or 0)
        cur_quota = float(a.get("quota_gb") or 0)
        if cands and act < len(cands):
            cur_quota = float(cands[act].get("quota_gb") or 0)
        txt, ok = QInputDialog.getDouble(self, "设置流量配额", "配额（GB，0=不限）:", cur_quota, 0, 1_000_000, 2)
        if not ok:
            return
        new_q = txt
        if cands and act < len(cands):
            cands[act]["quota_gb"] = new_q
            # 续费重置：清零封账 + 分配流量，重新启用（到期未过期即可，不管之前已用多少）
            cands[act]["used_gb"] = 0
            try:
                api_client.reset_flow(rid)
            except Exception:
                pass
            if _candidate_renewed(cands[act]):
                cands[act]["enabled"] = True
            creds["candidates"] = cands
            nd["_creds"] = creds
        relays = config.get("relays", []) or []
        for r in relays:
            if str(r.get("id", "")) == rid:
                cu = r.get("candidate_upstreams") or []
                if cu and act < len(cu):
                    cu[act]["quota_gb"] = new_q
                    cu[act]["used_gb"] = 0
                    if _candidate_renewed(cu[act]):
                        cu[act]["enabled"] = True
                    r["candidate_upstreams"] = cu
                break
        config.set_value("relays", relays)
        try:
            api_client.assign(a.get("user_id"), "relay", rid, nd, quota_gb=new_q)
        except ApiError as e:
            QMessageBox.warning(self, "设置失败", e.message)
            return
        # 之前可能因用尽被停服务，重新部署恢复代理
        self._redeploy_relay(rid)
        self._page.refresh()
        self.accept()

    def _manage_ips(self, a: dict) -> None:
        if a.get("resource_type") != "relay":
            QMessageBox.information(self, "提示", "只有中转线路支持管理 IP。")
            return
        dlg = _ManageIpsDialog(self._page, self._u, a, on_saved=self._reload)
        dlg.exec()
        self._reload()
        self._page.refresh()

    def _redeploy_relay(self, rid: str) -> None:
        """改配额/到期恢复后重新部署服务器（之前可能被到期/用尽 /stop 停了）。"""
        import subprocess
        from pathlib import Path

        from .workers import CallWorker

        relays = config.get("relays", []) or []
        relay = next((r for r in relays if str(r.get("id", "")) == str(rid)), None)
        if not relay:
            return
        sid = relay.get("server_id", "")
        srv_ip = ""
        if sid.startswith("manual:"):
            mid = sid[len("manual:"):]
            for m in (config.get("manual_servers", []) or []):
                if m.get("id") == mid:
                    srv_ip = m.get("host", "") or ""
                    break
        if not srv_ip:
            return
        root = Path(__file__).resolve().parent.parent.parent
        script = root / "scripts" / "apply_relays.py"

        def _do():
            r = subprocess.run(
                ["uv", "run", "python", str(script), sid, srv_ip],
                capture_output=True, text=True, timeout=900, cwd=str(root),
            )
            return r.stdout + r.stderr

        w = CallWorker(_do)
        if not hasattr(self, "_workers"):
            self._workers = []
        self._workers.append(w)
        w.finished.connect(w.deleteLater)
        w.start()

    def _save_col_widths(self, *args) -> None:
        config.set_value("user_lines_col_widths", [self._table.columnWidth(c) for c in range(8)])


class _UserDetailDialog(SizedDialog):
    _size_key = "user_detail_dialog_size"
    _default_w = 640
    _default_h = 520
    """用户详情/设置：编辑表单 + 名下分配（文字展示）。"""

    def __init__(self, parent=None, user: dict | None = None, resource_id: str = "", node_data: dict | None = None, resource_expiry: str = "", resource_type: str = "") -> None:
        super().__init__(parent)
        self._user = user or {}
        self.resource_id = resource_id
        self.node_data = node_data or {}
        self._resource_expiry = resource_expiry
        self.resource_type = resource_type or "vultr"
        self.setWindowTitle("用户详情 / 设置")
        self.setMinimumSize(720, 720)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        header = QLabel(
            f'<span style="font-size:16px;font-weight:700;color:#f2f2f7;">{self._user.get("username", "")}</span>'
        )
        layout.addWidget(header)

        form = QVBoxLayout()
        form.setSpacing(6)

        form.addWidget(self._lbl("用户名"))
        self.uname_edit = QLineEdit(self._user.get("username", ""))
        self.uname_edit.setReadOnly(True)  # 用户名是登录凭证，客户端不能改用户名，服务端也保持只读
        form.addWidget(self.uname_edit)

        form.addWidget(self._lbl("密码（留空 = 不修改）"))
        self.pwd_edit = QLineEdit()
        self.pwd_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.pwd_edit.setPlaceholderText("留空则不修改密码")
        form.addWidget(self.pwd_edit)

        form.addWidget(self._lbl("客户到期（默认 = 服务器到期，可调低但不能超过服务器到期）"))
        self.exp_edit = QLineEdit()
        client_exp = self.node_data.get("client_expiry", "") or ""
        if client_exp:
            try:
                exp_dt = datetime.fromisoformat(str(client_exp).replace("Z", "+00:00"))
                self.exp_edit.setText(exp_dt.astimezone().strftime("%Y-%m-%d"))
            except Exception:
                self.exp_edit.setText(str(client_exp)[:10])
        self.exp_edit.setPlaceholderText("留空 = 服务器到期")
        form.addWidget(self.exp_edit)

        form.addWidget(self._lbl("状态"))
        self.status_combo = QComboBox()
        self.status_combo.addItem("启用", "active")
        self.status_combo.addItem("禁用", "disabled")
        self.status_combo.setCurrentIndex(0 if self._user.get("status", "active") == "active" else 1)
        form.addWidget(self.status_combo)

        form.addWidget(self._lbl("每天 IP 数上限（默认 5，含初始 IP，可切换 = 上限-1）"))
        self.switch_spin = QSpinBox()
        self.switch_spin.setRange(0, 999)
        node_limit = int(self._user.get("node_switch_limit", 0) or 0)
        if node_limit > 0:
            self.switch_spin.setValue(node_limit)  # 每台服务器独立上限（已单独设置）
        else:
            self.switch_spin.setValue(int(self._user.get("switch_limit", 5) or 5))
        form.addWidget(self.switch_spin)

        form.addWidget(self._lbl("备注"))
        self.note_edit = QPlainTextEdit()
        self.note_edit.setFixedHeight(60)
        self.note_edit.setPlainText(self._user.get("note", ""))
        form.addWidget(self.note_edit)

        layout.addLayout(form)

        # 当前服务器信息（单台，只显示本次点击的这一台）
        srv_lbl = QLabel("当前服务器")
        srv_lbl.setStyleSheet("font-weight:600;color:#f2f2f7;")
        layout.addWidget(srv_lbl)

        node = self.node_data
        domain = node.get("domain", "") or node.get("host", "") or self.resource_id
        ip = node.get("ip", "") or "-"
        info = QLabel(f'{domain}  <span style="color:#98989d;">（{ip}）</span>')
        layout.addWidget(info)

        layout.addWidget(self._lbl("流量配额（GB，留空 = 不限额）"))
        self.quota_edit = QLineEdit()
        quota = float(node.get("quota_gb", 0) or 0)
        if quota:
            self.quota_edit.setText(f"{quota:.0f}")
        self.quota_edit.setPlaceholderText("留空=不限额")
        layout.addWidget(self.quota_edit)

        # 重置流量（客户续费时清零已用，重新给满配额）
        flow_btn = QPushButton("重置流量")
        flow_btn.setToolTip("清空该服务器的已用流量，重新给满配额（客户续费用）；重置前会提示账户剩余额度")
        flow_btn.clicked.connect(self._reset_flow)
        layout.addWidget(flow_btn)

        # 服务器到期（只读，在「服务器」页双击「到期时间」列设置）
        srv_expiry = config.get("server_expiry", {}).get(str(self.resource_id), "") or ""
        if not srv_expiry:
            srv_expiry = self._resource_expiry or ""
        exp_lbl = QLabel(f'服务器到期：<span style="color:#ff9f0a;">{str(srv_expiry)[:10] or "跟随账户"}</span>')
        layout.addWidget(exp_lbl)

        # 切换次数 + 重置
        sw_row = QHBoxLayout()
        self.sw_count_lbl = QLabel("今天已切换：— 次")
        sw_row.addWidget(self.sw_count_lbl)
        sw_row.addStretch()
        self.reset_btn = QPushButton("重置切换次数")
        self.reset_btn.setToolTip("重置该节点当天的切换计数")
        self.reset_btn.clicked.connect(self._reset_current_switch)
        sw_row.addWidget(self.reset_btn)
        layout.addLayout(sw_row)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self._on_ok)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

        self._load_switch_count()

    def _lbl(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet("color:#98989d;")
        return lbl

    def _plain(self, text: str) -> QTableWidgetItem:
        item = QTableWidgetItem(text)
        item.setFlags(Qt.ItemFlag.ItemIsEnabled)
        return item

    def _echo(self, msg: str) -> None:
        parent = self.parent()
        if hasattr(parent, "_log"):
            parent._log(msg)

    def _reset_current_switch(self) -> None:
        """重置当前服务器的当日切换计数。"""
        username = self._user.get("username", "")
        domain = self.node_data.get("domain", "") or self.resource_id
        ret = QMessageBox.question(
            self,
            "确认重置",
            f"确定重置用户「{username}」节点 {domain} 的切换次数吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if ret != QMessageBox.StandardButton.Yes:
            return
        try:
            api_client.reset_user_switch(self._user["id"], self.resource_id)
            self._echo(f"✅ 已重置节点 {domain} 的切换次数")
            self._load_switch_count()
        except ApiError as e:
            QMessageBox.warning(self, "重置失败", e.message)

    def _reset_flow(self) -> None:
        """重置当前服务器的已用流量（清零），客户续费时重新给满配额。

        重置前提示 Vultr 账户剩余额度，让管理员判断风险（超出账户额度的部分收 $0.01/GB）。
        """
        domain = self.node_data.get("domain", "") or self.resource_id
        # 查账户剩余额度
        tip = ""
        try:
            from ..vultr import VultrAPI, compute_account_bandwidth
            api = VultrAPI(config.get("vultr_api_key", ""))
            account = compute_account_bandwidth(api.get_account_bandwidth())
            remaining = float(account.get("remaining_gb", 0) or 0)
            if remaining > 0:
                tip = (
                    f"\n\n当前 Vultr 账户剩余额度：{remaining:.0f} GB（约 {remaining/1024:.1f} TB）\n"
                    "重置后客户可继续用流量；客户实际出站超出账户额度部分收 $0.01/GB。"
                )
        except Exception:
            pass
        ret = QMessageBox.question(
            self,
            "确认重置流量",
            f"确定重置节点 {domain} 的已用流量吗？\n\n已用流量将清零，客户重新拿到满额配额。{tip}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if ret != QMessageBox.StandardButton.Yes:
            return
        try:
            # 记录重置基准（当前实例出站），compute_bandwidth 从重置点重新算
            # 否则 sync_flow 5 分钟内会用「当前实例出站」覆盖 D1 used_gb，重置失效
            from ..vultr import VultrAPI
            api = VultrAPI(config.get("vultr_api_key", ""))
            try:
                cur_bw = api.get_instance_bandwidth(self.resource_id)
                cur_out = sum(day.get("outgoing_bytes", 0) for day in cur_bw.values()) / 1e9
            except Exception:
                cur_out = 0.0
            def _set_reset(m):
                m = dict(m or {})
                m[str(self.resource_id)] = cur_out
                return m
            config.mutate("server_flow_reset", _set_reset)
            # 清空本地累计 + D1 used_gb
            def _clear_cum(m):
                m = dict(m or {})
                m[str(self.resource_id)] = 0.0
                return m
            config.mutate("server_cumulative_used", _clear_cum)
            api_client.reset_flow(self.resource_id)
            self._echo(f"✅ 已重置节点 {domain} 的流量（已用清零，配额重新给满）")
        except ApiError as e:
            QMessageBox.warning(self, "重置失败", e.message)

    def _load_switch_count(self) -> None:
        """加载当前服务器的当日切换次数（已用 IP 数/上限，与客户端口径一致）。"""
        _today = datetime.now().strftime("%Y-%m-%d")  # 北京时间（与 Worker 端 bjToday 一致）
        count = 0
        limit = int(self._user.get("switch_limit", 5) or 5)
        try:
            data = api_client.get_user(self._user["id"])
            limit = int((data.get("user", {}) or {}).get("switch_limit", limit) or limit)
            for a in data.get("assignments", []):
                if str(a.get("resource_id", "")) == str(self.resource_id):
                    count = int(a.get("switch_count", 0) or 0)
                    sw_date = str(a.get("switch_date", "") or "")
                    if sw_date and sw_date[:10] != _today:
                        count = 0
                    break
        except ApiError:
            pass
        used = count + 1  # 含初始 IP（客户端口径：初始 IP 占 1 个额度）
        self.sw_count_lbl.setText(f"已用 IP：{used}/{limit}")

    def _on_ok(self) -> None:
        username = self.uname_edit.text().strip()
        password = self.pwd_edit.text()
        if not username:
            QMessageBox.warning(self, "提示", "用户名不能为空")
            return
        if password and len(password) < 6:
            QMessageBox.warning(self, "提示", "密码至少 6 位")
            return

        fields = {
            "status": self.status_combo.currentData(),
            "note": self.note_edit.toPlainText().strip(),
            "switch_limit": self.switch_spin.value(),
            # 每台服务器独立的上限：传 resource_id，Worker 会同步到 assignments.switch_limit（客户端限流依据）
            "resource_id": self.resource_id,
        }
        if password:
            fields["password"] = password

        # 客户到期（每台服务器独立，默认 = 服务器到期，不能超过服务器到期）
        srv_expiry = config.get("server_expiry", {}).get(str(self.resource_id), "") or self._resource_expiry or ""
        client_exp = ""
        exp_text = self.exp_edit.text().strip()
        if exp_text:
            exp_dt = _parse_dt(exp_text)
            if not exp_dt:
                QMessageBox.warning(self, "提示", "到期时间格式不对，请用 YYYY-MM-DD")
                return
            srv_dt = _parse_dt(srv_expiry) if srv_expiry else None
            if srv_dt and exp_dt > srv_dt:
                QMessageBox.warning(self, "提示", f"客户到期不能晚于服务器到期 {str(srv_expiry)[:10]}")
                return
            client_exp = exp_dt.strftime("%Y-%m-%d %H:%M:%S")

        # 流量配额（留空 = 不限额）
        new_quota = 0
        q_text = self.quota_edit.text().strip()
        if q_text:
            try:
                new_quota = float(q_text)
            except ValueError:
                QMessageBox.warning(self, "提示", "流量配额格式不正确，请输入数字（GB）")
                return
            if new_quota <= 0:
                QMessageBox.warning(self, "提示", "流量配额必须大于 0")
                return

        # 写回 node_data：客户到期 + 流量配额（每台服务器独立）
        node = dict(self.node_data)
        node_changed = False
        if client_exp and str(node.get("client_expiry", ""))[:10] != client_exp[:10]:
            node["client_expiry"] = client_exp
            node_changed = True
        elif not client_exp and node.get("client_expiry"):
            node["client_expiry"] = ""
            node_changed = True
        if float(node.get("quota_gb") or 0) != new_quota:
            node["quota_gb"] = new_quota
            node_changed = True
        if node_changed:
            try:
                # 显式传 quota_gb，让 Worker 更新 assignments.quota_gb 列（限流依据）
                api_client.assign(self._user["id"], self.resource_type, self.resource_id, node, quota_gb=new_quota)
            except ApiError as e:
                QMessageBox.warning(self, "保存失败", e.message)
                return

        try:
            api_client.update_user(self._user["id"], **fields)
            parts = []
            if password:
                parts.append("密码")
            if client_exp or node_changed:
                parts.append("客户到期/流量")
            self._echo(f"✅ 已保存用户「{username}」的修改（{'、'.join(parts) if parts else '用户名/状态/切换次数'}，客户端 30 秒内自动同步）")
            self.accept()
        except ApiError as e:
            self._echo(f"❌ 保存失败：{e.message}")
            QMessageBox.warning(self, "保存失败", e.message)


class _AssignDetailDialog(SizedDialog):
    _size_key = "assigndetail_dialog_size"
    _default_w = 680
    _default_h = 560
    """资源分配详情：已分配用户列表（用户名/备注/时间）+ 新增分配 + 取消分配。"""

    def __init__(self, parent, resource_type: str, resource_id: str, node_data: dict, resource_expiry: str = "", total_quota_gb: float = 0.0, used_gb: float = 0.0) -> None:
        super().__init__(parent)
        self.setWindowTitle("分配详情")
        self.setMinimumSize(920, 700)
        self.resource_type = resource_type
        self.resource_id = resource_id
        self.node_data = node_data
        self.resource_expiry = resource_expiry
        self.total_quota_gb = total_quota_gb
        self.used_gb = used_gb  # 服务器累计已用流量（出站）
        self.remaining_quota = total_quota_gb - used_gb
        self._assigned_ids: set[int] = set()

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        rt_name = "中转节点（relay）" if resource_type == "relay" else "Vultr 服务器节点"
        head = QLabel(
            f'<span style="font-size:15px;font-weight:700;color:#f2f2f7;">{rt_name}</span> '
            f'<span style="color:#0a84ff;font-family:monospace;">{resource_id}</span>'
        )
        layout.addWidget(head)

        if resource_expiry:
            exp_label = QLabel(f'资源原始到期：<span style="color:#ff9f0a;">{str(resource_expiry)[:10]}</span>')
            layout.addWidget(exp_label)

        # 已分配用户列表 + 新增分配 + 取消分配（vultr 单实例，无组内 IP 表格）
        self._ip_time_ids: list[str] = []
        self._ip_time_data: list = []

        self.count_label = QLabel("")
        self.count_label.setStyleSheet("color:#98989d;")
        layout.addWidget(self.count_label)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["用户名", "IP", "流量", "到期时间", "备注"])
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(40)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        hh = self.table.horizontalHeader()
        for c in range(5):
            hh.setSectionResizeMode(c, QHeaderView.ResizeMode.Interactive)
        hh.sectionResized.connect(self._on_assign_col_resized)
        defaults = [90, 120, 70, 100, 140]
        saved = config.get("assigndetail_table_widths", {}) or {}
        for c, d in enumerate(defaults):
            self.table.setColumnWidth(c, int(saved.get(str(c), d)))
        layout.addWidget(self.table, 1)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self.assign_btn = QPushButton("＋ 分配用户")
        self.assign_btn.setProperty("primary", True)
        self.assign_btn.clicked.connect(self._assign)
        btn_row.addWidget(self.assign_btn)
        btn_row.addStretch(1)
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.reject)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

        self._load()

    def _on_assign_col_resized(self, col: int, old: int, new: int) -> None:
        """已分配用户表格列宽调整持久化。"""
        widths = config.get("assigndetail_table_widths", {}) or {}
        widths[str(col)] = new
        config.set_value("assigndetail_table_widths", widths)

    def _save_note(self, edit) -> None:
        """保存备注到用户（编辑结束后触发）。"""
        uid = edit.property("user_id")
        if uid is None:
            return
        text = edit.text().strip()
        try:
            api_client.update_user(int(uid), note=text)
            global_echo(f"✅ 已保存备注：{text or '（清空）'}")
        except ApiError as e:
            QMessageBox.warning(self, "保存失败", e.message)
            self._load()

    def _load(self) -> None:
        import json as _json

        try:
            data = api_client.list_all_assignments()
        except ApiError as e:
            self.count_label.setText(f'<span style="color:#ff453a;">加载失败：{e.message}</span>')
            return
        # resource-assignments 端点不带 node_data，改用 all-assignments 再按资源过滤
        assigns = [a for a in data.get("assignments", [])
                   if a.get("resource_type") == self.resource_type and str(a.get("resource_id")) == str(self.resource_id)]
        self._assigned_ids = {int(a.get("user_id", 0)) for a in assigns}
        # 计算已分配流量
        assigned_quota = 0.0
        for a in assigns:
            nd = a.get("node_data", {}) or {}
            if isinstance(nd, str):
                try:
                    nd = _json.loads(nd)
                except Exception:
                    nd = {}
            try:
                assigned_quota += float(nd.get("quota_gb") or 0)
            except Exception:
                pass
        self.remaining_quota = self.total_quota_gb - self.used_gb - assigned_quota
        quota_text = ""
        if self.total_quota_gb > 0:
            quota_text = f" · 剩余流量 <b>{self.remaining_quota:.0f}G</b>"
        self.count_label.setText(f"已分配给 <b>{len(assigns)}</b> 个用户{quota_text}")
        # 每个分配一行（vultr 单实例）
        expanded = []
        for a in assigns:
            nd = a.get("node_data", {}) or {}
            if isinstance(nd, str):
                try:
                    nd = _json.loads(nd)
                except Exception:
                    nd = {}
            q = float(nd.get("quota_gb") or 0)
            entries = [{
                "ip": nd.get("ip", "") or "-",
                "expiry": _fmt_expires(nd.get("expiry", "")) if nd.get("expiry") else "-",
                "quota": q,
            }]
            expanded.append({
                "username": a.get("username", ""),
                "note": a.get("user_note", "") or a.get("note", "") or "",
                "user_id": a.get("user_id"),
                "entries": entries,
            })
        self.table.setRowCount(sum(len(e["entries"]) for e in expanded))
        row = 0
        for e in expanded:
            n = len(e["entries"])
            # 用户名：跨行合并居中
            user_item = QTableWidgetItem(e["username"])
            user_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 0, user_item)
            if n > 1:
                self.table.setSpan(row, 0, n, 1)
            # 每个分配一行：IP / 流量 / 到期 / 备注
            for i, entry in enumerate(e["entries"]):
                ip_item = QTableWidgetItem(entry["ip"])
                ip_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.table.setItem(row + i, 1, ip_item)
                quota_item = QTableWidgetItem(f"{entry['quota']:.0f}G" if entry["quota"] > 0 else "不限")
                quota_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.table.setItem(row + i, 2, quota_item)
                exp_item = QTableWidgetItem(entry["expiry"])
                exp_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.table.setItem(row + i, 3, exp_item)
                note_edit = QLineEdit(e["note"])
                note_edit.setPlaceholderText("输入备注")
                note_edit.setProperty("user_id", e["user_id"])
                note_edit.editingFinished.connect(lambda ed=note_edit: self._save_note(ed))
                self.table.setCellWidget(row + i, 4, note_edit)
            row += n

    def _assign(self) -> None:
        dlg = _UserPickerDialog(self, self._assigned_ids, self.resource_expiry, self.total_quota_gb, self.remaining_quota, self.resource_type)
        if dlg.exec() != QDialog.DialogCode.Accepted or dlg.user_id is None:
            return
        try:
            nd = dict(self.node_data)
            if self.total_quota_gb > 0:
                nd["quota_gb"] = dlg.quota_gb
            # 客户到期写到 node_data.client_expiry（每台服务器独立），不写用户级 expires_at
            if dlg.expires_at:
                nd["client_expiry"] = dlg.expires_at
            else:
                nd.pop("client_expiry", None)
            api_client.assign(dlg.user_id, self.resource_type, self.resource_id, nd, quota_gb=dlg.quota_gb)
            self._load()
        except ApiError as e:
            QMessageBox.warning(self, "分配失败", e.message)

class _UserPickerDialog(SizedDialog):
    _size_key = "user_picker_dialog_size"
    _default_w = 500
    _default_h = 360
    """选择要分配的用户（只列活跃用户）。"""

    def __init__(self, parent=None, exclude_ids: set[int] | None = None, resource_expiry: str = "", total_quota_gb: float = 0.0, remaining_quota: float = 0.0, resource_type: str = "", sub_info: dict | None = None, relay_hosts: list[str] | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("选择用户")
        self.setMinimumSize(560, 520 if (relay_hosts or []) else 360)
        self.user_id: int | None = None
        self.expires_at: str | None = None
        self.quota_gb: float = 0.0
        self._exclude_ids = exclude_ids or set()
        self._resource_expiry = resource_expiry
        self._total_quota_gb = total_quota_gb
        self._remaining_quota = remaining_quota
        self._resource_type = resource_type
        self._sub_info = sub_info or {}
        self._relay_hosts = relay_hosts or []

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        lbl = QLabel("选择要分配的用户")
        lbl.setStyleSheet("color:#98989d;")
        layout.addWidget(lbl)
        self.user_combo = QComboBox()
        layout.addWidget(self.user_combo)

        if resource_type == "relay":
            # relay：使用时长（墙钟时长）+ 独立流量配额 + 订阅信息
            info = self._sub_info
            _expiry_full = info.get("expiry") or ""
            expiry = _expiry_full[:10] if _expiry_full else "未加载"
            plan_total = info.get("plan_total_gb", 0) or 0
            used = info.get("used_gb", 0) or 0
            assigned = info.get("assigned_gb", 0) or 0
            remaining = info.get("remaining_gb", 0) or 0
            info_text = f'订阅到期 <b style="color:#ff9f0a;">{expiry}</b>'
            if plan_total > 0:
                info_text += (
                    f'　·　套餐流量 <b>{plan_total:g}GB</b>　·　'
                    f'已使用 <b style="color:#ff9f0a;">{used:.1f}GB</b>　·　'
                    f'已分配 <b>{assigned:.1f}GB</b>　·　'
                    f'总剩余 <b style="color:#32d74b;">{remaining:.1f}GB</b>'
                )
            else:
                info_text += '　·　<span style="color:#ff453a;">套餐未加载（请先在 Webshare-IP 页刷新）</span>'
            if info.get("mixed"):
                info_text += '　·　<span style="color:#ff9f0a;">⚠️ 混合类型按机房流量池计</span>'
            info_lbl = QLabel(info_text)
            info_lbl.setStyleSheet("color:#98989d;")
            layout.addWidget(info_lbl)

            if len(self._relay_hosts) > 1:
                # 多个 IP：每个 IP 单独设置配额/到期（含一键应用到全部）
                self._build_per_ip_grid(layout)
            else:
                dur_lbl = QLabel("使用时长（不能晚于订阅到期）")
                dur_lbl.setStyleSheet("color:#98989d;")
                layout.addWidget(dur_lbl)
                dur_row = QHBoxLayout()
                self.unit_combo = QComboBox()
                self.unit_combo.addItems(["按天", "按小时", "按分钟"])
                self.unit_combo.setCurrentIndex(0)  # 默认按天
                self.unit_combo.currentIndexChanged.connect(self._on_unit_changed)
                dur_row.addWidget(self.unit_combo)
                self.duration_spin = QSpinBox()
                self.duration_spin.setRange(*_UNIT_RANGES[0])
                self.duration_spin.setValue(1)
                self.duration_spin.setSuffix(_UNIT_SUFFIXES[0])
                dur_row.addWidget(self.duration_spin)
                dur_row.addStretch(1)
                layout.addLayout(dur_row)

                quota_lbl = QLabel("流量配额（GB，留空 = 不限额）" + (f"（剩余 {remaining:.1f}G）" if plan_total > 0 else ""))
                quota_lbl.setStyleSheet("color:#98989d;")
                layout.addWidget(quota_lbl)
                self.quota_edit = QLineEdit("10")  # 默认 10GB（可改；清空 = 不限额）
                self.quota_edit.setPlaceholderText(
                    f"如 100，不能大于剩余 {remaining:.1f}G；默认 10，清空 = 不限额"
                )
                layout.addWidget(self.quota_edit)
        else:
            if resource_expiry:
                exp_info = QLabel(f'资源原始到期：<span style="color:#ff9f0a;">{str(resource_expiry)[:10]}</span>')
                layout.addWidget(exp_info)

            if total_quota_gb > 0:
                quota_lbl = QLabel(f"流量配额（GB，留空 = 分配全部剩余 {remaining_quota:.0f}G）")
                quota_lbl.setStyleSheet("color:#98989d;")
                layout.addWidget(quota_lbl)
                self.quota_edit = QLineEdit()
                self.quota_edit.setPlaceholderText(f"如 100，不能大于剩余 {remaining_quota:.0f}G")
                layout.addWidget(self.quota_edit)

            exp_lbl = QLabel("用户到期时间" + (f"（留空 = 资源到期 {resource_expiry}）" if resource_expiry else "（留空 = 不过期）"))
            exp_lbl.setStyleSheet("color:#98989d;")
            layout.addWidget(exp_lbl)
            self.expiry_edit = QLineEdit()
            self.expiry_edit.setPlaceholderText("如 2026-12-31 23:59，不能晚于资源到期")
            layout.addWidget(self.expiry_edit)

        self.hint = QLabel("")
        self.hint.setStyleSheet("color:#6c6c70;font-size:12px;")
        layout.addWidget(self.hint)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self._on_ok)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

        self._load_users()

    def _on_unit_changed(self, idx: int) -> None:
        """切换时长单位时更新取值范围与默认值（按天 1-30 / 按小时 1-23 / 按分钟 5-59）。"""
        lo, hi = _UNIT_RANGES[idx]
        self.duration_spin.setRange(lo, hi)
        self.duration_spin.setSuffix(_UNIT_SUFFIXES[idx])
        self.duration_spin.setValue(lo)

    def _build_per_ip_grid(self, layout) -> None:
        """多个 IP：每个 IP 一行，独立设置配额(GB) 与到期时长；顶部统一值改动即同步到全部。"""
        self._per_ip_rows: list[tuple] = []
        uni = QHBoxLayout()
        uni.addWidget(QLabel("统一配额(GB,0=不限):"))
        self.uni_quota = QSpinBox()
        self.uni_quota.setRange(0, 1000000)
        self.uni_quota.setValue(10)  # 默认 10GB
        self.uni_quota.valueChanged.connect(lambda v: [spin.setValue(v) for spin, _, _ in self._per_ip_rows])
        uni.addWidget(self.uni_quota)
        uni.addWidget(QLabel("统一到期:"))
        self.uni_dur = QSpinBox()
        self.uni_dur.setRange(1, 9999)
        self.uni_dur.setValue(1)
        self.uni_dur.valueChanged.connect(lambda v: [dspin.setValue(v) for _, dspin, _ in self._per_ip_rows])
        uni.addWidget(self.uni_dur)
        self.uni_unit = QComboBox()
        self.uni_unit.addItems(["按天", "按小时", "按分钟", "不过期"])
        self.uni_unit.currentIndexChanged.connect(
            lambda _: [unit.setCurrentText(self.uni_unit.currentText()) for _, _, unit in self._per_ip_rows]
        )
        uni.addWidget(self.uni_unit)
        apply_btn = QPushButton("应用到全部")
        apply_btn.setProperty("compact", True)
        apply_btn.clicked.connect(self._apply_per_ip_all)
        uni.addWidget(apply_btn)
        uni.addStretch(1)
        layout.addLayout(uni)

        grid = QGridLayout()
        grid.addWidget(QLabel("上游 IP"), 0, 0)
        grid.addWidget(QLabel("配额(GB)"), 0, 1)
        grid.addWidget(QLabel("到期时长"), 0, 2)
        for i, host in enumerate(self._relay_hosts):
            grid.addWidget(QLabel(host or ""), i + 1, 0)
            q = QSpinBox()
            q.setRange(0, 1000000)
            q.setValue(self.uni_quota.value())
            q.setToolTip("0 = 不限流量")
            grid.addWidget(q, i + 1, 1)
            drow = QHBoxLayout()
            drow.setContentsMargins(0, 0, 0, 0)
            dspin = QSpinBox()
            dspin.setRange(1, 9999)
            dspin.setValue(self.uni_dur.value())
            unit = QComboBox()
            unit.addItems(["按天", "按小时", "按分钟", "不过期"])
            unit.setCurrentText(self.uni_unit.currentText())
            drow.addWidget(dspin)
            drow.addWidget(unit)
            dw = QWidget()
            dw.setLayout(drow)
            grid.addWidget(dw, i + 1, 2)
            self._per_ip_rows.append((q, dspin, unit))
        grid.setColumnStretch(2, 1)
        layout.addLayout(grid)

    def _apply_per_ip_all(self) -> None:
        q = self.uni_quota.value()
        d = self.uni_dur.value()
        u = self.uni_unit.currentText()
        for spin, _, _ in self._per_ip_rows:
            spin.setValue(q)
        for _, dspin, unit in self._per_ip_rows:
            dspin.setValue(d)
            unit.setCurrentText(u)

    def per_ip_results(self) -> list[dict]:
        """返回每个 IP 的 {quota_gb, expiry}（expiry 为绝对时间，不过期=空串）。"""
        out = []
        for q, dspin, unit in getattr(self, "_per_ip_rows", []):
            out.append({"quota_gb": q.value(), "expiry": _per_ip_expiry(dspin.value(), unit.currentText())})
        return out

    def _load_users(self) -> None:
        try:
            users = api_client.list_users().get("users", [])
        except ApiError as e:
            self.hint.setText(f'<span style="color:#ff453a;">加载失败：{e.message}</span>')
            return
        active = [u for u in users if u.get("status") == "active"]
        available = [u for u in active if int(u.get("id", 0)) not in self._exclude_ids]
        if not available:
            if not active:
                self.hint.setText('<span style="color:#ff9f0a;">暂无活跃用户，请先在「用户管理」页新增用户。</span>')
            else:
                self.hint.setText('<span style="color:#ff9f0a;">所有活跃用户都已分配此资源。</span>')
            return
        for u in available:
            label = u.get("username", "")
            if u.get("note"):
                label += f"（{u['note']}）"
            self.user_combo.addItem(label, u["id"])

    def _on_ok(self) -> None:
        data = self.user_combo.currentData()
        if data is None:
            QMessageBox.warning(self, "提示", "请先选择用户")
            return
        multi_ip = len(self._relay_hosts) > 1
        # 流量配额校验（仅 vultr 有配额输入框时；多 IP 每 IP 单独设置，不在此校验）
        if not multi_ip and hasattr(self, "quota_edit"):
            q_text = self.quota_edit.text().strip()
            if q_text:
                try:
                    q = float(q_text)
                except ValueError:
                    QMessageBox.warning(self, "提示", "流量配额格式不正确，请输入数字（GB）")
                    return
                if q <= 0:
                    QMessageBox.warning(self, "提示", "流量配额必须大于 0")
                    return
                # relay：不能超过订阅剩余流量
                if self._resource_type == "relay":
                    plan_total = self._sub_info.get("plan_total_gb", 0) or 0
                    remaining = self._sub_info.get("remaining_gb", 0) or 0
                    if plan_total > 0 and q > remaining + 1e-6:
                        QMessageBox.warning(self, "提示", f"流量配额不能大于剩余流量 {remaining:.1f}G")
                        return
                if self._total_quota_gb > 0 and q > self._remaining_quota + 1e-6:
                    QMessageBox.warning(self, "提示", f"流量配额不能大于剩余流量 {self._remaining_quota:.0f}G")
                    return
                self.quota_gb = q
            else:
                self.quota_gb = self._remaining_quota  # 留空 = 分配全部剩余
        if self._resource_type == "relay" and not multi_ip:
            unit_idx = self.unit_combo.currentIndex()
            seconds = self.duration_spin.value() * _UNIT_SECONDS[unit_idx]
            self.expires_at = _duration_expiry(seconds)
            sub_expiry = self._sub_info.get("expiry") or ""
            if sub_expiry:
                # 客户到期（完整时间戳）不能晚于订阅到期；订阅到期可能是完整时间戳或纯日期
                exp_cmp = self.expires_at if len(sub_expiry) > 10 else self.expires_at[:10]
                if exp_cmp > sub_expiry:
                    _max_txt = _max_settable_duration(sub_expiry)
                    QMessageBox.warning(self, "提示", f"使用时长超出订阅到期时间（{sub_expiry[:10]}），最多可设置 {_max_txt}，请缩短时长")
                    return
        elif hasattr(self, "expiry_edit"):
            exp_text = self.expiry_edit.text().strip()
            if exp_text:
                exp_dt = _parse_dt(exp_text)
                if exp_dt is None:
                    QMessageBox.warning(self, "提示", "到期时间格式不正确，请用 2026-12-31 23:59")
                    return
                if self._resource_expiry:
                    res_dt = _parse_dt(self._resource_expiry)
                    if res_dt and exp_dt > res_dt:
                        QMessageBox.warning(self, "提示", f"用户到期时间不能晚于资源到期时间 {self._resource_expiry}")
                        return
                self.expires_at = exp_text
            else:
                self.expires_at = self._resource_expiry or None
        else:
            self.expires_at = self._resource_expiry or None
        self.user_id = int(data)
        self.accept()


def assign_dialog(parent, resource_type: str, resource_id: str, node_data: dict, resource_expiry: str = "", total_quota_gb: float = 0.0, used_gb: float = 0.0) -> None:
    """打开资源分配详情弹窗（self-contained：查看 + 分配）。"""
    dlg = _AssignDetailDialog(parent, resource_type, resource_id, node_data, resource_expiry, total_quota_gb, used_gb)
    dlg.exec()
