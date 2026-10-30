"""中转节点页：展示已生成的中转节点（relay），复制节点（SS + SOCKS5 多协议 + 二维码）、删除。
名称/备注可编辑并持久化，协议链接和二维码跟随名称；列宽可拖持久化 + 行拖拽排序。"""

from __future__ import annotations

import base64
import re
import subprocess
import time
from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .. import api_client, config, ipqs
from ..relay import SOCKS_USER, SS_METHOD, hysteria_port, reality_port, relay_node_data, socks_port
from ..share import hysteria2_link, socks5_link, ss_link, vless_reality_link
from .drag_table import DragTableWidget
from .workers import CallWorker

_COLS = ["", "#", "名称", "类型", "评分", "前置服务器", "前置域名", "上游出口", "备注", "操作"]
_COL_DEFAULTS = {0: 30, 1: 35, 2: 110, 3: 45, 4: 130, 5: 110, 6: 150, 7: 125, 8: 85}
_OP_COL = len(_COLS) - 1  # 操作列索引
_SEL_COL = 0              # 勾选列（checkbox，批量分配用）

# 回收页列（回收时间 + 被回收用户 + 手动回归）
_RECYCLE_COLS = ["#", "名称", "类型", "回收时间", "被回收用户", "上游出口", "备注", "操作"]
_RECYCLE_OP_COL = len(_RECYCLE_COLS) - 1


def _make_qr(link: str, size: int = 150) -> QPixmap:
    """生成链接的二维码 QPixmap。"""
    import qrcode

    qr = qrcode.QRCode(border=2, box_size=8)
    qr.add_data(link)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    data = img.tobytes("raw", "RGB")
    qimg = QImage(data, img.size[0], img.size[1], QImage.Format_RGB888)
    return QPixmap.fromImage(qimg).scaled(
        size, size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
    )


class QrDialog(QDialog):
    """二维码弹窗：大二维码 + 复制二维码图片（方便粘贴发给客户扫码导入）。"""

    def __init__(self, name: str, link: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"二维码 — {name}")
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        self._pix = _make_qr(link, 260)
        qr_label = QLabel()
        qr_label.setPixmap(self._pix)
        layout.addWidget(qr_label, 0, Qt.AlignmentFlag.AlignCenter)

        tip = QLabel("手机扫描此二维码可直接导入代理软件")
        tip.setStyleSheet("color:#6e6e73;")
        tip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(tip)

        btn_row = QHBoxLayout()
        self.copy_img_btn = QPushButton("复制二维码")
        self.copy_img_btn.setProperty("primary", True)
        self.copy_img_btn.clicked.connect(self._copy_img)
        btn_row.addWidget(self.copy_img_btn)
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.reject)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

    def _copy_img(self) -> None:
        QApplication.clipboard().setPixmap(self._pix)
        self.copy_img_btn.setText("已复制 ✓")


class NodeShareDialog(QDialog):
    """复制节点：展示 SS + SOCKS5 两个协议，含字段、复制链接、二维码。"""

    def __init__(self, relay: dict, server_ip: str, parent=None):
        super().__init__(parent)
        name = relay.get("name", "")
        relay_port = int(relay.get("relay_port", 0))
        relay_pw = relay.get("relay_password", "")
        sp = socks_port(relay_port)
        self.setWindowTitle(f"复制节点 — {name}")
        self.setMinimumWidth(640)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        header = QLabel(f'<span style="font-size:16px;font-weight:700;color:#f2f2f7;">{name}</span>')
        header.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(header)

        if not server_ip or server_ip == "未知":
            warn = QLabel('<span style="color:#ff9f0a;">前置服务器不存在，无法生成连接信息</span>')
            layout.addWidget(warn)
            close = QPushButton("关闭")
            close.clicked.connect(self.reject)
            layout.addWidget(close)
            return

        # SS 协议（remark 用名称，跟随列表里的名称）
        ss = ss_link(server_ip, relay_port, SS_METHOD, relay_pw, name)
        layout.addWidget(self._card(
            "Shadowsocks（SS）", "加密代理，大陆直连穿透 GFW",
            [("地址", server_ip, False), ("端口", str(relay_port), False),
             ("加密", SS_METHOD, False), ("密码", relay_pw, True)],
            ss, name,
        ))

        # SOCKS5 协议
        socks = socks5_link(server_ip, sp, SOCKS_USER, relay_pw, f"{name}-socks5")
        layout.addWidget(self._card(
            "SOCKS5", "明文带认证，海外/软路由客户用",
            [("地址", server_ip, False), ("端口", str(sp), False),
             ("用户名", SOCKS_USER, False), ("密码", relay_pw, True)],
            socks, name,
        ))

        # VLESS-Reality 协议
        rp_real = reality_port(relay_port)
        uuid = relay.get("reality_uuid", "")
        sni = relay.get("reality_sni", "www.cloudflare.com")
        pub_key = relay.get("reality_public_key", "")
        short_id = relay.get("reality_short_id", "")
        if uuid and pub_key:
            real = vless_reality_link(
                server_ip, rp_real, uuid, "xtls-rprx-vision",
                sni, "chrome", pub_key, short_id, name,
            )
            layout.addWidget(self._card(
                "VLESS-Reality", "TLS 伪装 Cloudflare，抗封锁",
                [("地址", server_ip, False), ("端口", str(rp_real), False),
                 ("SNI", sni, False), ("UUID", uuid, True)],
                real, name,
            ))

        # Hysteria2 协议（UDP）
        hp = hysteria_port(relay_port)
        hyst_pw = relay.get("hysteria_password", "")
        if hyst_pw:
            hyst = hysteria2_link(server_ip, hp, hyst_pw, "", name)
            layout.addWidget(self._card(
                "Hysteria2", "UDP 暴力加速，晚高峰抗丢包",
                [("地址", server_ip, False), ("端口", str(hp), False),
                 ("密码", hyst_pw, True)],
                hyst, name,
            ))

        close = QPushButton("关闭")
        close.clicked.connect(self.reject)
        layout.addWidget(close)

    def _card(self, section: str, desc: str, fields: list, link: str, name: str) -> QFrame:
        card = QFrame()
        card.setProperty("card", True)
        lay = QVBoxLayout(card)
        lay.setSpacing(8)

        title = QLabel(f'<span style="color:#0a84ff;font-size:13px;font-weight:600;">{section}</span>'
                       f'<span style="color:#98989d;font-size:11px;">　{desc}</span>')
        lay.addWidget(title)

        body = QHBoxLayout()
        body.setSpacing(16)
        fcol = QVBoxLayout()
        fcol.setSpacing(6)
        for label, value, is_secret in fields:
            fcol.addLayout(self._kv(label, value, is_secret))
        body.addLayout(fcol, 1)
        lay.addLayout(body)

        link_row = QHBoxLayout()
        link_label = QLabel(link)
        link_label.setStyleSheet("color:#98989d;font-size:11px;")
        link_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        link_label.setWordWrap(True)
        link_row.addWidget(link_label, 1)
        copy_btn = QPushButton("复制链接")
        copy_btn.setProperty("primary", True)
        copy_btn.clicked.connect(lambda _=False: self._copy(link, copy_btn))
        link_row.addWidget(copy_btn)
        qr_btn = QPushButton("二维码")
        qr_btn.clicked.connect(lambda _=False: QrDialog(f"{section} - {name}", link, self).exec())
        link_row.addWidget(qr_btn)
        lay.addLayout(link_row)
        return card

    def _kv(self, label: str, value: str, is_secret: bool):
        row = QHBoxLayout()
        row.setSpacing(8)
        lbl = QLabel(f'<span style="color:#98989d;">{label}</span>')
        lbl.setFixedWidth(60)
        val = QLabel(value)
        val.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        val.setStyleSheet("color:#32d74b;" if is_secret else "color:#f2f2f7;")
        copy = QPushButton("复制")
        copy.setProperty("compact", True)
        copy.setFixedHeight(24)
        copy.clicked.connect(lambda _=False, v=value, b=copy: self._copy(v, b))
        row.addWidget(lbl)
        row.addWidget(val, 1)
        row.addWidget(copy)
        return row

    def _copy(self, text: str, btn=None) -> None:
        QApplication.clipboard().setText(text)
        if btn is not None:
            btn.setText("已复制 ✓")


def _sync_recycle_log(relays: list[dict]) -> None:
    """把 /admin/recycle-log 的自动回收记录合并进本地：从 config.relays 删掉 + 记 recycled_relays。

    Worker cron 到期/流量用尽会自动停服务 + 删分配 + 记 recycle_log，但不改本地 config；
    这里刷新时拉回收日志，把对应 relay 从 config.relays 移除并记进回收冷却列表。
    """
    from datetime import datetime, timedelta, timezone

    try:
        rl = api_client.list_recycle_log()
    except Exception:
        return
    entries = rl.get("recycle", []) or []
    if not entries:
        return
    # 同一 relay 可能多次回收→回归→再回收，取最新一条（entries 按 id 降序）
    by_id: dict[str, dict] = {}
    for e in entries:
        by_id.setdefault(str(e.get("resource_id", "")), e)
    recycled = config.get("recycled_relays", []) or []
    recycled_ids = {str(x.get("id", "")) for x in recycled}
    to_remove: list[str] = []
    tz = timezone(timedelta(hours=8))
    for r in relays:
        rid = str(r.get("id", ""))
        e = by_id.get(rid)
        if not e or rid in recycled_ids:
            continue
        created = e.get("created_at", "")
        try:
            dt = datetime.fromisoformat(str(created).replace("Z", "+00:00"))
            recycled_at = dt.astimezone(tz).strftime("%Y-%m-%d %H:%M")
        except Exception:
            recycled_at = str(created)[:16]
        recycled.append({
            "id": rid,
            "host": r.get("host", ""),
            "port": r.get("port", ""),
            "name": r.get("name", ""),
            "ip_type": r.get("ip_type", "dc"),
            "recycled_at": recycled_at,
            "recycled_from_user": e.get("username", "") or "",
        })
        recycled_ids.add(rid)
        to_remove.append(rid)
    if to_remove:
        config.set_value("recycled_relays", recycled)
        config.set_value("relays", [r for r in relays if str(r.get("id", "")) not in to_remove])
        relays[:] = [r for r in relays if str(r.get("id", "")) not in to_remove]


class NodesPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._workers: list[CallWorker] = []
        self._relays: list[dict] = []
        self._dc_relays: list[dict] = []
        self._res_relays: list[dict] = []
        self._recycled_relays: list[dict] = []
        self._assigned_ids: set[str] = set()  # D1 里已分配的 relay_id（从可用页移除）
        self._selected: dict[str, dict] = {}  # 勾选的 relay：relay_id -> relay dict（批量分配）
        self._suppress = False
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(16)

        title = QLabel("中转节点")
        title.setStyleSheet("font-size: 20px; font-weight: 700;")
        root.addWidget(title)

        card = QFrame()
        card.setProperty("card", True)
        cc = QVBoxLayout(card)
        cc.setContentsMargins(20, 16, 20, 16)
        cc.setSpacing(10)

        top = QHBoxLayout()
        hint = QLabel("勾选多个 IP 后点「分配给用户」一次分给一个客户（1 IP = 1 用户）；双击「名称」「备注」可编辑。")
        hint.setStyleSheet("color: #98989d;")
        top.addWidget(hint, 1)
        self.assign_btn = QPushButton("分配给用户（已选 0）")
        self.assign_btn.setProperty("primary", True)
        self.assign_btn.clicked.connect(self._assign_selected)
        top.addWidget(self.assign_btn)
        self.refresh_btn = QPushButton("刷新")
        self.refresh_btn.clicked.connect(self.refresh)
        top.addWidget(self.refresh_btn)
        cc.addLayout(top)

        self.status = QLabel("")
        cc.addWidget(self.status)
        root.addWidget(card)

        self.tabs = QTabWidget()
        self.dc_table = self._make_table("nodes_dc_col_widths")
        self.res_table = self._make_table("nodes_res_col_widths")
        self.recycle_table = self._make_table("nodes_recycle_col_widths", _RECYCLE_COLS, _RECYCLE_OP_COL)
        self.tabs.addTab(self.dc_table, "数据中心 IP")
        self.tabs.addTab(self.res_table, "静态住宅 IP")
        self.tabs.addTab(self.recycle_table, "回收 IP")
        self.tabs.currentChanged.connect(self._update_tab_titles)
        root.addWidget(self.tabs, 1)

    def _make_table(self, width_key: str, cols: list[str] | None = None, op_col: int | None = None) -> DragTableWidget:
        """建一个中转节点表格，列宽按 width_key 独立持久化。cols/op_col 可覆盖（回收页用不同列）。"""
        cols = cols or _COLS
        op_col = _OP_COL if op_col is None else op_col
        table = DragTableWidget(0, len(cols))
        table.setHorizontalHeaderLabels(cols)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setAlternatingRowColors(True)
        table.verticalHeader().setDefaultSectionSize(52)  # 行高 52，按钮完整显示不被垂直截断
        table.verticalHeader().setVisible(False)
        hdr = table.horizontalHeader()
        hdr.setStretchLastSection(True)
        hdr.setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        # 内容列 Interactive（自由拖列宽 + 持久化），操作列 stretch last（填剩余，大屏无横滚）
        for c in range(op_col):
            hdr.setSectionResizeMode(c, QHeaderView.ResizeMode.Interactive)
        if cols is _COLS:
            for c, w in _COL_DEFAULTS.items():
                table.setColumnWidth(c, w)
        table._suppress_resize = True
        saved = config.get(width_key, {})
        items = []
        for c, w in saved.items():
            try:
                c = int(c)
                if c < op_col and 0 < int(w) < 600:
                    items.append((c, int(w)))
            except (ValueError, IndexError):
                pass
        # 总和校验：持久化列宽总和超 2000 才忽略（防止拖太宽后操作列被挤出；中文名+长域名需要更宽）
        if sum(w for _, w in items) <= 2000:
            for c, w in items:
                table.setColumnWidth(c, w)
        table._suppress_resize = False
        table._width_key = width_key
        hdr.sectionResized.connect(self._on_col_resized)
        table.row_dropped.connect(self._on_row_dropped)
        table.itemChanged.connect(self._on_item_changed)
        table.cellDoubleClicked.connect(self._on_cell_dblclick)
        return table

    def _update_tab_titles(self) -> None:
        self.tabs.setTabText(0, f"数据中心 IP（{len(self._dc_relays)}）")
        self.tabs.setTabText(1, f"静态住宅 IP（{len(self._res_relays)}）")
        self.tabs.setTabText(2, f"回收 IP（{len(self._recycled_relays)}）")

    def activate(self) -> None:
        self.refresh()

    # ---------- 加载 ----------

    @staticmethod
    def _load_server_map() -> dict[str, tuple[str, str]]:
        """server_id -> (ip, name)。Vultr running 实例 + manual_servers。"""
        mapping: dict[str, tuple[str, str]] = {}
        key = config.get("vultr_api_key", "")
        if key:
            try:
                from ..vultr import VultrAPI

                api = VultrAPI(key)
                for inst in api.list_instances():
                    if inst.get("power_status") == "running":
                        mapping[inst["id"]] = (inst.get("main_ip", ""), inst.get("label", inst["id"]))
            except Exception:
                pass
        for m in config.get("manual_servers", []):
            mapping[f"manual:{m['id']}"] = (m.get("host", ""), m.get("name", "手动节点"))
        return mapping

    @staticmethod
    def _sort_relays(relays: list[dict]) -> list[dict]:
        order = config.get("nodes_order", []) or []
        if not order:
            return relays
        order_map = {rid: i for i, rid in enumerate(order)}
        return sorted(relays, key=lambda r: order_map.get(r.get("id"), len(order) + 9999))

    def refresh(self) -> None:
        self.refresh_btn.setEnabled(False)
        self.status.setText('<span style="color:#0a84ff;">加载中…</span>')

        def _do():
            relays = self._sort_relays(config.get("relays", []))
            server_map = self._load_server_map()
            assigned: set[str] = set()
            try:
                data = api_client.list_all_assignments()
                assigned = {str(a.get("resource_id", "")) for a in data.get("assignments", [])
                            if a.get("resource_type") == "relay"}
            except Exception:
                pass  # Worker 不可达时降级：不按已分配过滤，全部显示
            # 同步自动回收日志：把 Worker 自动回收（到期/流量用尽）的 relay 标记进回收页
            try:
                _sync_recycle_log(relays)
            except Exception:
                pass  # 同步失败不阻断加载
            return relays, server_map, assigned

        w = CallWorker(_do)
        w.done.connect(self._on_loaded)
        w.failed.connect(lambda m: self._on_failed(str(m)))
        self._workers.append(w)
        w.finished.connect(w.deleteLater)
        w.start()

    def _on_loaded(self, result) -> None:
        self.refresh_btn.setEnabled(True)
        relays, server_map, assigned = result
        self._relays = relays
        self._server_map = server_map
        self._assigned_ids = assigned
        self._fill()
        self.status.setText(f'<span style="color:#32d74b;">共 {len(relays)} 个中转节点（已分配 {len(assigned)} 个）</span>')

    def _on_failed(self, msg: str) -> None:
        self.refresh_btn.setEnabled(True)
        self.status.setText(f'<span style="color:#ff453a;">加载失败：{msg}</span>')

    def _fill(self) -> None:
        server_map = getattr(self, "_server_map", {})
        assigned = getattr(self, "_assigned_ids", set())
        # 可用池：未分配（回收 = 取消中转已删 config 记录，不再出现在这里）
        available = [r for r in self._relays if r.get("id") not in assigned]
        self._dc_relays = [r for r in available if r.get("ip_type", "dc") != "res"]
        self._res_relays = [r for r in available if r.get("ip_type", "dc") == "res"]
        self._recycled_relays = config.get("recycled_relays", []) or []
        self._suppress = True
        self._fill_table(self.dc_table, self._dc_relays, server_map)
        self._fill_table(self.res_table, self._res_relays, server_map)
        self._fill_recycle_table(self.recycle_table, self._recycled_relays, server_map)
        self._suppress = False
        self._update_tab_titles()

    def _fill_table(self, table: DragTableWidget, relays: list, server_map: dict) -> None:
        table.setRowCount(0)
        table.setRowCount(len(relays))
        for row, r in enumerate(relays):
            name = r.get("name", "")
            ip_type = r.get("ip_type", "dc")
            kind = "住宅" if ip_type == "res" else "数据中心"
            sid = r.get("server_id", "")
            srv_ip, srv_name = server_map.get(sid, ("未知", "未知"))
            server_text = srv_ip if srv_ip != "未知" else "未知服务器"
            upstream = f"{r.get('host','')}:{r.get('port','')}"

            # 勾选列（checkbox，批量分配）
            chk = QTableWidgetItem()
            chk.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            chk.setCheckState(Qt.CheckState.Checked if r["id"] in self._selected else Qt.CheckState.Unchecked)
            chk.setData(Qt.ItemDataRole.UserRole, r["id"])
            table.setItem(row, _SEL_COL, chk)

            self._item(table, row, 1, str(row + 1))

            name_item = QTableWidgetItem(name)
            name_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            name_item.setData(Qt.ItemDataRole.UserRole, r["id"])
            name_item.setToolTip("双击编辑名称（复制节点的链接和二维码会跟随）")
            table.setItem(row, 2, name_item)

            self._item(table, row, 3, kind)
            # 评分（从 webshare_scores 读，key = host:port）
            ip_key = f"{r.get('host','')}:{r.get('port','')}"
            scores = config.get("webshare_scores", {})
            score_rec = scores.get(ip_key)
            if score_rec is not None:
                score_val = score_rec.get("score")
                ts_str = score_rec.get("time_str") or ""
                score_item = QTableWidgetItem(f"{score_val}分\n{ts_str}")
                score_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if score_val < 35:
                    score_item.setForeground(Qt.GlobalColor.green)
                elif score_val < 75:
                    score_item.setForeground(Qt.GlobalColor.yellow)
                else:
                    score_item.setForeground(Qt.GlobalColor.red)
                score_item.setToolTip(f"fraud_score={score_val}")
            else:
                score_item = QTableWidgetItem("-")
                score_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            table.setItem(row, 4, score_item)
            self._item(table, row, 5, server_text)
            if srv_ip != "未知":
                table.item(row, 5).setToolTip(f"{srv_name}（{srv_ip}）")
            # 前置域名（客户连接的入口域名，复制节点里的地址用这个）
            entry_domain = r.get("entry_domain", "")
            domain_item = QTableWidgetItem(entry_domain or "-")
            domain_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if entry_domain:
                domain_item.setToolTip("客户连接使用此域名（复制节点里的地址）")
            table.setItem(row, 6, domain_item)
            self._item(table, row, 7, upstream)

            # 备注（共用 webshare_notes，key = host:port，与 webshare-IP 页一致）
            notes = config.get("webshare_notes", {})
            note_item = QTableWidgetItem(notes.get(ip_key, ""))
            note_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            note_item.setData(Qt.ItemDataRole.UserRole, ip_key)
            note_item.setToolTip("双击编辑备注（与 webshare-IP 页共用）")
            table.setItem(row, 8, note_item)

            w = QWidget()
            lay = QHBoxLayout(w)
            lay.setContentsMargins(4, 2, 4, 2)
            lay.addStretch(1)
            copy_btn = QPushButton("复制节点")
            copy_btn.setProperty("compact", True)
            copy_btn.setFixedHeight(30)
            entry = r.get("entry_domain", "") or srv_ip
            copy_btn.clicked.connect(
                lambda _=False, rel=r, e=entry: self._share(rel, e)
            )
            lay.addWidget(copy_btn)
            # 检测按钮（检测上游 IP 质量，与 webshare-IP 页共用评分数据源）
            host = r.get("host", "")
            rem = ipqs.ipqs_remaining()
            check_btn = QPushButton(f"检测(剩{rem})")
            check_btn.setProperty("compact", True)
            check_btn.setFixedHeight(30)
            check_btn.setToolTip("IPQS 检测上游 IP 质量（与 webshare-IP 页共用评分）")
            check_btn.setEnabled(rem > 0)
            check_btn.clicked.connect(
                lambda _=False, b=check_btn, h=host, k=ip_key: self._check_ip(h, k, b)
            )
            lay.addWidget(check_btn)
            recycle_btn = QPushButton("回收")
            recycle_btn.setProperty("compact", True)
            recycle_btn.setFixedHeight(30)
            recycle_btn.setToolTip("停服务并进回收页冷却（不删记录，可手动回归）")
            recycle_btn.clicked.connect(lambda _=False, rid=r["id"]: self._recycle(rid))
            lay.addWidget(recycle_btn)
            lay.addStretch(1)
            table.setCellWidget(row, _OP_COL, w)

    def _fill_recycle_table(self, table: DragTableWidget, relays: list, server_map: dict) -> None:
        """回收页：显示回收的 relay（回收时间 + 被回收用户 + 手动回归）。"""
        table.setRowCount(0)
        table.setRowCount(len(relays))
        for row, r in enumerate(relays):
            ip_type = r.get("ip_type", "dc")
            kind = "住宅" if ip_type == "res" else "数据中心"
            upstream = f"{r.get('host','')}:{r.get('port','')}"
            self._item(table, row, 0, str(row + 1))
            self._item(table, row, 1, r.get("name", ""))
            self._item(table, row, 2, kind)
            self._item(table, row, 3, r.get("recycled_at", ""))
            self._item(table, row, 4, r.get("recycled_from_user", ""))
            self._item(table, row, 5, upstream)
            # 备注（可编辑，共用 webshare_notes，key = host:port，回归后 webshare-IP 页可见）
            ip_key = f"{r.get('host','')}:{r.get('port','')}"
            notes = config.get("webshare_notes", {})
            note_item = QTableWidgetItem(notes.get(ip_key, ""))
            note_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            note_item.setData(Qt.ItemDataRole.UserRole, ip_key)
            note_item.setToolTip("双击编辑备注（与 webshare-IP 页共用，回归后可见）")
            table.setItem(row, 6, note_item)
            w = QWidget()
            lay = QHBoxLayout(w)
            lay.setContentsMargins(4, 2, 4, 2)
            lay.addStretch(1)
            return_btn = QPushButton("手动回归")
            return_btn.setProperty("compact", True)
            return_btn.setProperty("primary", True)
            return_btn.setFixedHeight(30)
            return_btn.setToolTip("回归到 webshare 未中转列表，可重新生成中转再分配")
            return_btn.clicked.connect(lambda _=False, rid=r["id"]: self._return_recycled(rid))
            lay.addWidget(return_btn)
            lay.addStretch(1)
            table.setCellWidget(row, _RECYCLE_OP_COL, w)

    def _update_selected_label(self) -> None:
        self.assign_btn.setText(f"分配给用户（已选 {len(self._selected)}）")

    def _assign_selected(self) -> None:
        """批量分配：把勾选的所有 IP 分配给一个用户（选用户 + 使用时长 + 订阅约束）。"""
        from ..api_client import ApiError
        from ..webshare import subscription_quota_info
        from .users_page import _UserPickerDialog

        if not self._selected:
            self.status.setText('<span style="color:#ff9f0a;">请先勾选要分配的 IP</span>')
            return
        # 确定类型（混合时按机房算，弹窗会提示）
        ip_types = {r.get("ip_type", "dc") for r in self._selected.values()}
        ip_type = "res" if ip_types == {"res"} else "dc"
        sub_info = subscription_quota_info(ip_type)
        sub_info["mixed"] = len(ip_types) > 1
        dlg = _UserPickerDialog(self, resource_type="relay", sub_info=sub_info)
        if dlg.exec() != QDialog.DialogCode.Accepted or dlg.user_id is None:
            return
        switch_api_key = str(config.get("switch_api_key", "") or "")
        server_map = getattr(self, "_server_map", {})
        ok = fail = 0
        for relay in self._selected.values():
            sid = relay.get("server_id", "")
            srv_ip = server_map.get(sid, ("未知", ""))[0]
            if not srv_ip or srv_ip == "未知":
                fail += 1
                continue
            nd = relay_node_data(relay, srv_ip, switch_api_key)
            if dlg.expires_at:
                nd["client_expiry"] = dlg.expires_at
            nd["quota_gb"] = dlg.quota_gb
            try:
                api_client.assign(dlg.user_id, "relay", relay["id"], nd, quota_gb=dlg.quota_gb)
                ok += 1
            except ApiError:
                fail += 1
        self._selected.clear()
        self._update_selected_label()
        self.refresh()
        color = "#32d74b" if fail == 0 else "#ff9f0a"
        self.status.setText(f'<span style="color:{color};">已分配 {ok} 个 IP' + (f"，{fail} 个失败" if fail else "") + '</span>')

    def _return_recycled(self, rid: str) -> None:
        """手动回归 = 解除冷却：从回收记录删掉，IP 回 webshare 未中转列表并标记「刚回归」。"""
        recycled = config.get("recycled_relays", []) or []
        target = next((r for r in recycled if str(r.get("id", "")) == str(rid)), None)
        if target:
            returned = config.get("returned_ips", {}) or {}
            returned[f"{target.get('host','')}:{target.get('port','')}"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            config.set_value("returned_ips", returned)
        recycled = [r for r in recycled if str(r.get("id", "")) != str(rid)]
        config.set_value("recycled_relays", recycled)
        self.refresh()

    def _item(self, table: DragTableWidget, row: int, col: int, text: str) -> None:
        it = QTableWidgetItem(str(text))
        it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        table.setItem(row, col, it)

    # ---------- 名称/备注编辑 ----------

    def _on_cell_dblclick(self, row: int, col: int) -> None:
        table = self.sender()
        if not isinstance(table, DragTableWidget):
            return
        if getattr(table, "_width_key", "") == "nodes_recycle_col_widths":
            if col == 6:  # 回收页备注
                table.editItem(table.item(row, col))
        elif col in (2, 8):  # 名称、备注
            table.editItem(table.item(row, col))

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if self._suppress:
            return
        col = item.column()
        table = item.tableWidget()
        is_recycle = getattr(table, "_width_key", "") == "nodes_recycle_col_widths"
        if not is_recycle and col == _SEL_COL:
            # 勾选/取消 → 更新 self._selected
            rid = item.data(Qt.ItemDataRole.UserRole)
            if not rid:
                return
            if item.checkState() == Qt.CheckState.Checked:
                for r in self._relays:
                    if r["id"] == rid:
                        self._selected[rid] = r
                        break
            else:
                self._selected.pop(rid, None)
            self._update_selected_label()
            return
        if is_recycle:
            if col != 6:  # 回收页只有备注可编辑
                return
        elif col not in (2, 8):  # 名称、备注
            return
        key = item.data(Qt.ItemDataRole.UserRole)
        if not key:
            return
        text = item.text().strip()
        if is_recycle:
            # 回收页备注：写 webshare_notes（key = host:port，回归后 webshare-IP 页可见）
            notes = config.get("webshare_notes", {})
            if text:
                notes[key] = text
            else:
                notes.pop(key, None)
            config.set_value("webshare_notes", notes)
            return
        if col == 2:
            # 名称：写 relay 的 name（key = relay id）
            relays = config.get("relays", [])
            for r in relays:
                if r["id"] == key:
                    r["name"] = text
                    break
            config.set_value("relays", relays)
        else:
            # 备注：写 webshare_notes（共用，key = host:port）
            notes = config.get("webshare_notes", {})
            if text:
                notes[key] = text
            else:
                notes.pop(key, None)
            config.set_value("webshare_notes", notes)
        for r in self._relays:
            if r["id"] == key:
                if col == 2:
                    r["name"] = text
                else:
                    r["note"] = text
                break

    # ---------- 列宽 / 行拖拽排序 ----------

    def _on_col_resized(self, col: int, old: int, new: int) -> None:
        table = self.sender()
        if getattr(table, "_suppress_resize", False):
            return
        width_key = getattr(table, "_width_key", "nodes_dc_col_widths")
        widths = config.get(width_key, {})
        widths[str(col)] = new
        config.set_value(width_key, widths)

    def _on_row_dropped(self, src_row: int, dst_row: int) -> None:
        table = self.sender()
        pool = self._res_relays if table is self.res_table else self._dc_relays
        if src_row < 0 or dst_row < 0 or src_row >= len(pool) or src_row == dst_row:
            return
        item = pool.pop(src_row)
        insert_at = dst_row if dst_row <= len(pool) else len(pool)
        pool.insert(insert_at, item)
        # 合并回 self._relays（数据中心在前、静态住宅在后），并持久化顺序
        merged = self._dc_relays + self._res_relays
        self._relays = merged
        config.set_value("nodes_order", [r["id"] for r in merged])
        self._fill()

    # ---------- 复制 / 删除 ----------

    def _share(self, relay: dict, server_ip: str) -> None:
        dlg = NodeShareDialog(relay, server_ip, self)
        dlg.exec()

    def _delete(self, rid: str) -> None:
        """取消中转：从 config 删除 + 清理服务器（gost + sing-box + ufw），确保不再转发。"""
        relays = config.get("relays", [])
        relay = next((r for r in relays if r["id"] == rid), None)
        if not relay:
            self.refresh()
            return
        relay_port = int(relay.get("relay_port", 0))
        socks_p = relay_port + 10000
        server_id = relay.get("server_id", "")
        server_map = getattr(self, "_server_map", {})
        server_ip = next((ip for sid, (ip, _) in server_map.items() if sid == server_id), "")
        # 保留完整凭据到 pool（同样 IP 重新生成时全复用，客户端不用改）
        pw_pool = config.get("relay_password_pool", {})
        pw_pool[f"{relay.get('host','')}:{relay.get('port','')}"] = {
            "relay_password": relay.get("relay_password", ""),
            "reality_uuid": relay.get("reality_uuid", ""),
            "reality_private_key": relay.get("reality_private_key", ""),
            "reality_public_key": relay.get("reality_public_key", ""),
            "reality_short_id": relay.get("reality_short_id", ""),
            "hysteria_password": relay.get("hysteria_password", ""),
        }
        config.set_value("relay_password_pool", pw_pool)
        # 从 config 删除
        relays = [r for r in relays if r["id"] != rid]
        config.set_value("relays", relays)
        # 从拖拽顺序里剔除该 IP（取消中转后回到 webshare-IP 页按官网顺序插回）
        _order_key = f"webshare_{relay.get('ip_type', 'dc')}_order"
        _order = config.get(_order_key, []) or []
        _ipk = f"{relay.get('host','')}:{relay.get('port','')}"
        if _ipk in _order:
            _order.remove(_ipk)
            config.set_value(_order_key, _order)
        # per-relay sing-box：取消中转只删该 relay 的独立 sing-box 服务（不再重建单进程 config）
        sb_cmd = (f"systemctl disable --now sing-box-relay-{rid} 2>/dev/null; "
                  f"rm -f /etc/systemd/system/sing-box-relay-{rid}.service /etc/sing-box/relay-{rid}.json; ")
        self.refresh()
        if not server_ip:
            self.status.setText('<span style="color:#ff453a;">已从配置取消，但找不到前置服务器 IP，需手动清理服务器</span>')
            return
        # root 密码（manual 服务器用自身密码，Vultr 用 server_configs）
        root_pw = config.get("root_password", "")
        if server_id.startswith("manual:"):
            mid = server_id[len("manual:"):]
            for m in config.get("manual_servers", []):
                if m.get("id") == mid:
                    root_pw = m.get("password", "") or root_pw
                    break
        else:
            sc = config.get("server_configs", {}).get(server_id, {})
            root_pw = sc.get("root_password") or root_pw

        def _do():
            if not root_pw:
                return "ERROR: 缺少 root 密码"
            cmd = (
                f"systemctl disable --now gost-relay-{rid} 2>/dev/null; "
                f"rm -f /etc/systemd/system/gost-relay-{rid}.service; "
                f"systemctl daemon-reload; "
                f"ufw delete allow {relay_port}/tcp 2>/dev/null; "
                f"ufw delete allow {relay_port}/udp 2>/dev/null; "
                f"ufw delete allow {socks_p}/tcp 2>/dev/null; "
                f"ufw delete allow {socks_p}/udp 2>/dev/null; "
                f"ufw delete allow {reality_port(relay_port)}/tcp 2>/dev/null; "
                f"ufw delete allow {reality_port(relay_port)}/udp 2>/dev/null; "
                f"ufw delete allow {hysteria_port(relay_port)}/udp 2>/dev/null; "
                f"{sb_cmd}"
                f"echo DONE"
            )
            r = subprocess.run(
                ["sshpass", "-p", root_pw, "ssh", "-o", "StrictHostKeyChecking=no",
                 "-o", "ConnectTimeout=15", f"root@{server_ip}", cmd],
                capture_output=True, text=True, timeout=120,
            )
            return r.stdout + r.stderr

        self.status.setText('<span style="color:#0a84ff;">正在从服务器取消中转…</span>')
        w = CallWorker(_do)
        w.done.connect(self._on_cancelled)
        w.failed.connect(lambda m: self._on_cancelled(f"失败：{m}"))
        self._workers.append(w)
        w.finished.connect(w.deleteLater)
        w.start()

    def _recycle(self, rid: str) -> None:
        """手动回收 = 取消中转 + 记回收冷却：删 config + 清服务器 + 进回收页。"""
        relays = config.get("relays", [])
        relay = next((r for r in relays if r["id"] == rid), None)
        if not relay:
            self.refresh()
            return
        name = relay.get("name", "") or rid
        ret = QMessageBox.question(
            self, "回收 IP",
            f"确定回收「{name}」吗？\n\n回收后：取消中转（停服务、释放服务器资源），IP 进「回收页」冷却，可手动回归重新生成中转。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if ret != QMessageBox.StandardButton.Yes:
            return
        # 记回收冷却（先保存 relay 信息，_delete 会删 config.relays）
        recycled = config.get("recycled_relays", []) or []
        if not any(str(x.get("id", "")) == str(rid) for x in recycled):
            recycled.append({
                "id": rid,
                "host": relay.get("host", ""),
                "port": relay.get("port", ""),
                "name": name,
                "ip_type": relay.get("ip_type", "dc"),
                "recycled_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "recycled_from_user": "未分配",
            })
            config.set_value("recycled_relays", recycled)
        # 取消中转（删 config + 清服务器，复用 _delete）
        self._delete(rid)
        self.refresh()

    def _on_cancelled(self, text: str) -> None:
        ok = "DONE" in str(text)
        color = "#32d74b" if ok else "#ff453a"
        self.status.setText(f'<span style="color:{color};">已取消中转（{"服务器已清理" if ok else "需检查"}）</span>')

    def _check_ip(self, host: str, ip_key: str, btn: QPushButton) -> None:
        """IPQS 检测上游 IP 质量（与 webshare-IP 页共用 webshare_scores）。"""
        if not re.match(r"^\d{1,3}(\.\d{1,3}){3}$", host):
            btn.setText("域名")
            btn.setToolTip("骨干域名无法检测具体 IP")
            return
        if ipqs.ipqs_remaining() <= 0:
            btn.setEnabled(False)
            return
        btn.setEnabled(False)
        btn.setText("检测中…")

        def _do():
            r = ipqs.check_ip(host)
            if r.get("quota"):
                return ("quota", r.get("message", "IPQS 拒绝"))
            return ("ok", (r.get("score"), r.get("proxy"), r.get("vpn"),
                           r.get("tor"), r.get("abuse")))

        w = CallWorker(_do)
        w.done.connect(lambda res: self._on_ip_checked(ip_key, btn, res))
        w.failed.connect(lambda m: self._on_ip_checked(ip_key, btn, ("fail", str(m))))
        self._workers.append(w)
        w.finished.connect(w.deleteLater)
        w.start()

    def _on_ip_checked(self, ip_key: str, btn: QPushButton, res) -> None:
        if res is None or res[0] == "fail":
            btn.setEnabled(True)
            btn.setText("失败")
            btn.setToolTip(f"IPQS 检测失败：{res[1] if res else '网络或 key 问题'}")
            return
        kind, data = res
        if kind == "quota":
            limit = int(config.get("ipqs_daily_limit", 35))
            config.set_value("ipqs_usage", {"date": ipqs.bj_today(), "count": limit})
            btn.setText("超额")
            btn.setToolTip(f"IPQS 每日额度已用完：{data}")
            self._fill()
            return
        score, proxy, vpn, tor, abuse = data
        scores = config.get("webshare_scores", {})
        scores[ip_key] = {"score": score, "proxy": proxy, "vpn": vpn, "tor": tor,
                          "abuse": abuse, "ts": time.time(),
                          "time_str": time.strftime("%Y-%m-%d %H:%M:%S")}
        config.set_value("webshare_scores", scores)
        ipqs.bump_ipqs_usage()
        self._fill()
