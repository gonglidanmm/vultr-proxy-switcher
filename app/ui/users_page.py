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
    QHBoxLayout,
    QHeaderView,
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

from .. import api_client, config
from ..api_client import ApiError

# 全局回显：让 Vultr-IP 页面的操作也能写入用户管理页的日志区
_g_echo = None


def set_global_echo(fn) -> None:
    global _g_echo
    _g_echo = fn


def global_echo(msg: str) -> None:
    if _g_echo is not None:
        _g_echo(msg)


COLS = ["用户名", "名称", "区域", "IP", "域名", "流量/状态", "已使用", "分配时间", "原始创建", "原始到期", "客户到期", "操作"]


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

        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # 行拖拽排序（持久化到 config user_order）
        self.table.setDragDropMode(QTableWidget.DragDropMode.InternalMove)
        self.table.setDragEnabled(True)
        self.table.setAcceptDrops(True)
        self.table.setDropIndicatorShown(True)
        self.table.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(48)
        header = self.table.horizontalHeader()
        header.setStretchLastSection(True)
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        for c in range(len(COLS) - 1):
            header.setSectionResizeMode(c, QHeaderView.ResizeMode.Interactive)
        self.table.setColumnWidth(0, 120)
        self.table.setColumnWidth(1, 120)
        self.table.setColumnWidth(2, 95)
        self.table.setColumnWidth(3, 120)
        self.table.setColumnWidth(4, 150)
        self.table.setColumnWidth(5, 85)
        self.table.setColumnWidth(6, 85)
        self.table.setColumnWidth(7, 95)
        self.table.setColumnWidth(8, 95)
        self.table.setColumnWidth(9, 95)
        self.table.setColumnWidth(10, 95)
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
        self.table.model().rowsMoved.connect(self._on_rows_moved)
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
            self._vultr_expiry, self._vultr_detail = self._compute_vultr_dynamic()
            self._backfill_user_expiry(assigns)
            self.stats_label.setText(
                f"共 {st.get('total_users', 0)} 个用户 · 活跃 {st.get('active_users', 0)} · "
                f"已分配 {st.get('assigned_users', 0)} 人 · 分配项 {st.get('total_assignments', 0)}"
            )
            self._populate()
        except ApiError as e:
            self.stats_label.setText(f'<span style="color:#ff453a;">加载失败：{e.message}</span>')
            self.table.setRowCount(0)

    def _compute_vultr_dynamic(self) -> tuple:
        """动态拉取 Vultr 到期日期 + 每实例详情。返回 (expiry_date, {iid: {name,region,ip,domain,bandwidth}})。"""
        from ..vultr import VultrAPI, compute_billing, compute_bandwidth, region_label
        from .. import domain as domain_mod
        key = config.get("vultr_api_key", "")
        if not key:
            return "", {}
        try:
            api = VultrAPI(key)
            instances = api.list_instances()
            account = api.get_account()
            plans = api.get_plans()
            billing = compute_billing(instances, plans, account)
            days = billing.get("days_left")
            expiry = ""
            if days:
                from datetime import timedelta
                expiry = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")
            bandwidth = compute_bandwidth(instances, plans, api)
            server_names = config.get("server_names", {})
            detail_map = {}
            for inst in instances:
                iid = inst.get("id", "")
                name = server_names.get(iid, "")
                dom = domain_mod.server_domain(iid)
                if not name:
                    name = ("Vultr-" + dom.split(".")[0]) if dom else inst.get("label", iid)
                bw = bandwidth.get(iid, {})
                used = bw.get("used_gb", 0)
                quota = bw.get("quota_gb", 0)
                used_text = f"{used * 1000:.0f}M" if used < 1 else f"{used:.1f}G"
                bw_text = f"{used_text} / {quota:.0f}G" if quota > 0 else used_text
                detail_map[iid] = {
                    "name": name,
                    "region": region_label(inst.get("region", "")),
                    "ip": inst.get("main_ip", ""),
                    "domain": dom or "",
                    "bandwidth": bw_text,
                    "used_gb": used,
                    "quota_gb": quota,
                }
            return expiry, detail_map
        except Exception:
            return "", {}

    def _backfill_user_expiry(self, assigns: list[dict]) -> None:
        """刷新时同步节点 node_data.expiry 为该服务器的到期时间（服务器页设置），fallback 账户动态。"""
        for a in assigns:
            uid = a.get("user_id")
            rid = a.get("resource_id")
            rtype = a.get("resource_type")
            if not uid:
                continue
            if rtype != "vultr":
                continue
            # 资源到期 = 该服务器在「服务器页」设置的到期时间；没设则 fallback 账户动态
            res_expiry = config.get("server_expiry", {}).get(str(rid), "") or self._vultr_expiry or ""
            if not res_expiry:
                continue

            # 同步节点 node_data.expiry 为该服务器的到期时间（UPSERT 重新 assign）
            nd = a.get("node_data") or {}
            if isinstance(nd, str):
                import json as _json
                try:
                    nd = _json.loads(nd)
                except Exception:
                    nd = {}
            if str(nd.get("expiry", ""))[:10] != res_expiry:
                nd["expiry"] = res_expiry
                try:
                    api_client.assign(uid, rtype, rid, nd)
                except Exception:
                    pass  # 静默失败

    def _vultr_used_text(self, detail: dict) -> str:
        """返回 vultr 实例的已使用流量（出站，真实值）。"""
        used = float(detail.get("used_gb", 0) or 0)
        if used <= 0:
            return "-"
        return f"{used * 1000:.0f}M" if used < 1 else f"{used:.1f}G"

    def _populate(self) -> None:
        # 按保存的拖拽顺序排序分配项（未出现在 order 里的放末尾）
        order = config.get("user_order", []) or []
        if order:
            order_map = {aid: i for i, aid in enumerate(order)}
            self._assigns.sort(key=lambda a: order_map.get(a.get("id"), len(order) + 9999))
        self.table.setRowCount(len(self._assigns))
        for row, a in enumerate(self._assigns):
            node = a.get("node_data", {}) or {}
            detail = self._vultr_detail.get(a["resource_id"], {})
            name = detail.get("name", "") or node.get("name", "") or node.get("host", "")
            location = detail.get("region", "") or node.get("location", "")
            ip = detail.get("ip", "") or node.get("ip", "")
            domain = detail.get("domain", "") or node.get("domain", "")
            q = node.get("quota_gb") or 0
            if q:
                traffic = f"配额 {float(q):.0f}G"
            else:
                traffic = detail.get("bandwidth", "") or node.get("bandwidth", "")
            # 完整时间（tooltip 用），列表显示 [:10] 短日期
            full_assign = a.get("created_at", "") or ""
            full_created = node.get("created_at", "") or ""
            # 原始到期 = 该服务器在「服务器页」设置的到期时间；没设则 fallback 账户动态到期
            _rid = str(a.get("resource_id", ""))
            full_expiry = config.get("server_expiry", {}).get(_rid, "") or self._vultr_expiry or ""
            # 客户到期 = 每台服务器独立的 client_expiry（默认 = 服务器到期）
            full_user_exp = node.get("client_expiry", "") or full_expiry or ""

            user_expiry = full_user_exp[:10] or "-"
            res_expiry = full_expiry[:10] or "-"
            assign_time = full_assign[:10] or "-"
            res_created = full_created[:10] or "-"

            cells = [
                a.get("username", ""),
                name,
                location,
                ip,
                domain,
                traffic,
                self._vultr_used_text(detail),
                assign_time,
                res_created,
                res_expiry,
                user_expiry,
            ]
            tips = {
                7: _fmt_full(full_assign),
                8: _fmt_full(full_created),
                9: _fmt_full(full_expiry),
                10: _fmt_full(full_user_exp),
            }
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setForeground(Qt.GlobalColor.white)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if col in tips:
                    item.setToolTip(tips[col])
                if col == 0:
                    item.setData(Qt.ItemDataRole.UserRole, a.get("id"))
                self.table.setItem(row, col, item)

            self.table.setCellWidget(row, 11, self._ops(a, row))

    def _on_rows_moved(self, parent, start, end, dest, row) -> None:
        """行拖拽后：按表格当前顺序重排 self._assigns + 持久化顺序。"""
        order = []
        id_to_assign = {a.get("id"): a for a in self._assigns}
        for r in range(self.table.rowCount()):
            item = self.table.item(r, 0)
            aid = item.data(Qt.ItemDataRole.UserRole) if item else None
            if aid in id_to_assign:
                order.append(aid)
        if len(order) != len(self._assigns):
            return  # 顺序读不完整（异常），不保存
        self._assigns = [id_to_assign[aid] for aid in order]
        config.set_value("user_order", order)

    def _on_cell_dbl(self, row: int, col: int) -> None:
        """双击用户名 → 编辑该用户。"""
        if col != 0 or row < 0 or row >= len(self._assigns):
            return
        a = self._assigns[row]
        user = {
            "id": a.get("user_id"),
            "username": a.get("username", ""),
            "status": a.get("user_status", "active"),
            "expires_at": a.get("user_expires_at"),
            "note": a.get("user_note", ""),
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
        """序号列选整行、其它格选该格、表格外空白取消。拖拽：强制移动（不复制）。"""
        if obj is self.table.viewport() and event.type() == QEvent.Type.DragMove:
            # macOS 上 Qt 拖拽默认可能走 CopyAction 导致复制行，强制 MoveAction
            event.setDropAction(Qt.DropAction.MoveAction)
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
        return {
            "id": a.get("user_id"),
            "username": a.get("username", ""),
            "status": a.get("user_status", "active"),
            "expires_at": a.get("user_expires_at"),
            "note": a.get("user_note", ""),
            "switch_limit": int(a.get("user_switch_limit", 5) or 5),
        }

    def _ops(self, a: dict, row: int) -> QWidget:
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        detail = QPushButton("详情/设置")
        detail.setProperty("compact", True)
        detail.setFixedHeight(26)
        detail.clicked.connect(lambda _=False, a=a: self._detail(a))
        lay.addWidget(detail)

        unassign = QPushButton("取消分配")
        unassign.setProperty("compact", True)
        unassign.setFixedHeight(26)
        unassign.clicked.connect(lambda _=False, a=a: self._unassign_assign(a))
        lay.addWidget(unassign)

        toggle = QPushButton("禁用" if a.get("user_status") == "active" else "启用")
        toggle.setProperty("compact", True)
        toggle.setFixedHeight(26)
        toggle.clicked.connect(lambda _=False, a=a: self._toggle_status(self._assign_to_user(a)))
        lay.addWidget(toggle)

        delete = QPushButton("删除")
        delete.setProperty("compact", True)
        delete.setFixedHeight(26)
        delete.clicked.connect(lambda _=False, a=a: self._delete_user(self._assign_to_user(a)))
        lay.addWidget(delete)
        return w

    def _unassign_assign(self, a: dict) -> None:
        node = a.get("node_data", {}) or {}
        name = node.get("name", "") or a.get("resource_id", "")
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
        resource_expiry = _user_resource_expiry(user["id"]) or self._vultr_expiry
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

    def _toggle_status(self, user: dict) -> None:
        new_status = "disabled" if user.get("status", "active") == "active" else "active"
        try:
            api_client.update_user(user["id"], status=new_status)
            label = "禁用" if new_status == "disabled" else "启用"
            self._log(f"✅ 已{label}用户「{user.get('username', '')}」（客户端 30 秒内自动同步）")
            self.refresh()
        except ApiError as e:
            self._log(f"❌ 操作失败：{e.message}")
            QMessageBox.warning(self, "操作失败", e.message)

    def _delete_user(self, user: dict) -> None:
        ret = QMessageBox.question(
            self, "删除用户",
            f"确定删除用户「{user.get('username', '')}」吗？\n其名下所有分配会一并删除，不可恢复。",
        )
        if ret != QMessageBox.StandardButton.Yes:
            return
        try:
            api_client.delete_user(user["id"])
            self._log(f"✅ 已删除用户「{user.get('username', '')}」及其名下分配")
            self.refresh()
        except ApiError as e:
            self._log(f"❌ 删除失败：{e.message}")
            QMessageBox.warning(self, "删除失败", e.message)

    def _detail(self, a: dict) -> None:
        user = self._assign_to_user(a)
        node_data = a.get("node_data", {}) or {}
        if isinstance(node_data, str):
            import json as _json
            try:
                node_data = _json.loads(node_data)
            except Exception:
                node_data = {}
        # 客户到期（用户级）上限 = 该用户名下最早到期的服务器（最早到期那台到期了，客户至少断一台）
        resource_expiry = _user_resource_expiry(user["id"]) or self._vultr_expiry
        dlg = _UserDetailDialog(self, user, a.get("resource_id", ""), node_data, resource_expiry)
        dlg.exec()
        self.refresh()

    def activate(self) -> None:
        self.refresh()


class _UserDialog(QDialog):
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


class _UserDetailDialog(QDialog):
    """用户详情/设置：编辑表单 + 名下分配（文字展示）。"""

    def __init__(self, parent=None, user: dict | None = None, resource_id: str = "", node_data: dict | None = None, resource_expiry: str = "") -> None:
        super().__init__(parent)
        self._user = user or {}
        self.resource_id = resource_id
        self.node_data = node_data or {}
        self._resource_expiry = resource_expiry
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
            "username": username,
            "status": self.status_combo.currentData(),
            "note": self.note_edit.toPlainText().strip(),
            "switch_limit": self.switch_spin.value(),
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
        if client_exp and node.get("client_expiry") != client_exp:
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
                api_client.assign(self._user["id"], "vultr", self.resource_id, node, quota_gb=new_quota)
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


class _AssignDetailDialog(QDialog):
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

        rt_name = "Vultr 服务器节点"
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
        dlg = _UserPickerDialog(self, self._assigned_ids, self.resource_expiry, self.total_quota_gb, self.remaining_quota)
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

class _UserPickerDialog(QDialog):
    """选择要分配的用户（只列活跃用户）。"""

    def __init__(self, parent=None, exclude_ids: set[int] | None = None, resource_expiry: str = "", total_quota_gb: float = 0.0, remaining_quota: float = 0.0) -> None:
        super().__init__(parent)
        self.setWindowTitle("选择用户")
        self.setMinimumSize(500, 320)
        self.user_id: int | None = None
        self.expires_at: str | None = None
        self.quota_gb: float = 0.0
        self._exclude_ids = exclude_ids or set()
        self._resource_expiry = resource_expiry
        self._total_quota_gb = total_quota_gb
        self._remaining_quota = remaining_quota

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        lbl = QLabel("选择要分配的用户")
        lbl.setStyleSheet("color:#98989d;")
        layout.addWidget(lbl)
        self.user_combo = QComboBox()
        layout.addWidget(self.user_combo)

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
        # 流量配额校验（仅 vultr 有配额输入框时）
        if hasattr(self, "quota_edit"):
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
                if self._total_quota_gb > 0 and q > self._remaining_quota + 1e-6:
                    QMessageBox.warning(self, "提示", f"流量配额不能大于剩余流量 {self._remaining_quota:.0f}G")
                    return
                self.quota_gb = q
            else:
                self.quota_gb = self._remaining_quota  # 留空 = 分配全部剩余
        if hasattr(self, "expiry_edit"):
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
