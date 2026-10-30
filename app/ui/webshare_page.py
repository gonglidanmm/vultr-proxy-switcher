"""Webshare-IP 管理页：加载 webshare 的代理服务器 + 静态住宅列表，
勾选生成中转节点（relay）部署到前置服务器；列表含序号/备注/是否中转/取消中转。"""

from __future__ import annotations

import base64
import json
import re
import subprocess
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import cloudflare, config, ipqs, vultr, webshare
from ..relay import RELAY_PORT_START, gen_relay_id, gen_relay_password, hysteria_port, next_relay_port, reality_port
from ..gencreds import generate_reality
from .drag_table import DragTableWidget
from .workers import CallWorker

_COLS = ["选择", "#", "地址", "端口", "国家", "城市", "套餐", "状态", "标记", "评分", "备注", "操作"]
_COL_DEFAULTS = {0: 45, 1: 40, 2: 140, 3: 60, 4: 50, 5: 100, 6: 80, 7: 50, 8: 60, 9: 130, 10: 110}

# 城市英文 → 中文（webshare 机房/住宅 IP 的城市，小写归一化匹配；未知城市回退原文）
CITY_NAMES_CN = {
    "ashburn": "阿什本", "asburn": "阿什本",
    "boca raton": "博卡拉顿",
    "buffalo": "布法罗",
    "charlotte": "夏洛特",
    "dallas": "达拉斯",
    "la": "洛杉矶", "los angeles": "洛杉矶",
    "melbourne": "墨尔本",
    "new york": "纽约", "ny": "纽约",
    "san francisco": "旧金山",
}


def _cn_country(code: str) -> str:
    """国家代码 → 中文名（未知回退原代码）。"""
    return vultr.country_name(code.lower())


def _cn_city(name: str) -> str:
    """城市英文 → 中文名（未知回退原文；空返回空）。"""
    return CITY_NAMES_CN.get(name.strip().lower(), name) if name else ""
_NOTE_COL = 10
_OP_COL = 11
_OP_WIDTH = 175  # 操作列固定宽度（检测 + 取消中转按钮）
_PAGE_SIZE = 20   # 每页显示的 IP 数


def _bj_today() -> str:
    """北京时间今天的日期（UTC+8 固定偏移，与本机时区无关）。"""
    return ipqs.bj_today()


class BatchGenerateDialog(QDialog):
    """批量生成中转：选国家/城市/数量/方式（随机或顺序）。"""

    def __init__(self, countries: list, country_cities: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("批量生成中转")
        self.setMinimumWidth(400)
        self._country_cities = country_cities
        lay = QVBoxLayout(self)
        lay.setSpacing(12)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.country_combo = QComboBox()
        self.country_combo.addItem("全部国家", "")
        for c in countries:
            self.country_combo.addItem(c, c)
        self.country_combo.currentIndexChanged.connect(self._on_country_changed)
        form.addRow("国家", self.country_combo)
        self.city_combo = QComboBox()
        self.city_combo.addItem("全部城市", "")
        form.addRow("城市", self.city_combo)
        self.count_spin = QSpinBox()
        self.count_spin.setRange(1, 1000)
        self.count_spin.setValue(1)
        form.addRow("数量", self.count_spin)
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("按顺序（列表前 N 个）", "seq")
        self.mode_combo.addItem("随机选 N 个", "random")
        form.addRow("选择方式", self.mode_combo)
        lay.addLayout(form)
        self.hint = QLabel("")
        self.hint.setStyleSheet("color: #8e8e93;")
        self.hint.setWordWrap(True)
        lay.addWidget(self.hint)
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        btn_row.addWidget(cancel)
        ok = QPushButton("生成中转")
        ok.setProperty("primary", True)
        ok.clicked.connect(self.accept)
        btn_row.addWidget(ok)
        lay.addLayout(btn_row)
        self._on_country_changed()

    def _on_country_changed(self, *_args):
        country = self.country_combo.currentData()
        cities = self._country_cities.get(country, set()) if country else set()
        self.city_combo.blockSignals(True)
        self.city_combo.clear()
        self.city_combo.addItem("全部城市", "")
        for c in sorted(cities):
            self.city_combo.addItem(c, c)
        self.city_combo.blockSignals(False)


class _ServerCard(QFrame):
    """前置服务器中转卡片：点击筛选列表；名称可改名（同步到 Vultr）。"""

    clicked = Signal(str)  # server_id
    rename_requested = Signal(str)  # server_id

    def __init__(self, srv_id: str, srv_ip: str, srv_name: str, count: int, keys: list[str]):
        super().__init__()
        self._srv_id = srv_id
        self.setProperty("card", True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(6)
        top = QHBoxLayout()
        top.setSpacing(8)
        name_lbl = QLabel(srv_name)
        name_lbl.setStyleSheet("font-weight: 600; font-size: 14px; color: #f2f2f7;")
        top.addWidget(name_lbl)
        ip_lbl = QLabel(srv_ip)
        ip_lbl.setStyleSheet("color: #8e8e93; font-size: 12px;")
        top.addWidget(ip_lbl)
        rename_btn = QPushButton("✏️ 改名")
        rename_btn.setProperty("compact", True)
        rename_btn.setFixedHeight(24)
        rename_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        rename_btn.clicked.connect(lambda _=False: self.rename_requested.emit(self._srv_id))
        top.addWidget(rename_btn)
        top.addStretch(1)
        badge = QLabel(f"{count} 个")
        badge.setStyleSheet(
            "background: #0a84ff; color: #ffffff; border-radius: 10px;"
            "padding: 3px 10px; font-weight: 600; font-size: 12px;"
        )
        top.addWidget(badge)
        lay.addLayout(top)
        ips_lbl = QLabel("、".join(keys))
        ips_lbl.setStyleSheet("color: #8e8e93; font-size: 12px;")
        ips_lbl.setWordWrap(True)
        lay.addWidget(ips_lbl)

    def mousePressEvent(self, e):
        self.clicked.emit(self._srv_id)
        super().mousePressEvent(e)


class DomainDialog(QDialog):
    """生成中转前选择入口域名：Cloudflare 主域名 + 现有域名下拉框（或手动新建二级域名）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("选择入口域名")
        self.setMinimumWidth(460)
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        tip = QLabel("客户连接将使用这个域名（而不是服务器 IP），换服务器/换 IP 时改 DNS 即可，客户无感。多个 IP 可选同一域名，靠端口区分。")
        tip.setStyleSheet("color: #98989d;")
        tip.setWordWrap(True)
        layout.addWidget(tip)

        zone_lbl = QLabel("Cloudflare 域名")
        zone_lbl.setStyleSheet("color: #98989d;")
        layout.addWidget(zone_lbl)
        self.zone_combo = QComboBox()
        self.zone_combo.setEditable(True)
        token = config.get("cf_token", "")
        default_zone = config.get("domain_name", "")
        if token:
            try:
                zones = cloudflare.list_zones(token)
                for z in zones:
                    self.zone_combo.addItem(z["name"], z["id"])
                if not zones and default_zone:
                    self.zone_combo.addItem(default_zone)
            except Exception:
                if default_zone:
                    self.zone_combo.addItem(default_zone)
        elif default_zone:
            self.zone_combo.addItem(default_zone)
        layout.addWidget(self.zone_combo)

        # 现有域名下拉框（读取主域名下所有 A 记录的二级/三级域名）
        existing_lbl = QLabel("现有域名（可选）")
        existing_lbl.setStyleSheet("color: #98989d;")
        layout.addWidget(existing_lbl)
        self.existing_combo = QComboBox()
        layout.addWidget(self.existing_combo)

        # 或手动填二级域名（新建）
        sub_lbl = QLabel("或新建二级域名")
        sub_lbl.setStyleSheet("color: #98989d;")
        layout.addWidget(sub_lbl)
        self.sub_edit = QLineEdit()
        self.sub_edit.setPlaceholderText("如 relay / node / dallas1")
        layout.addWidget(self.sub_edit)

        self.preview = QLabel("")
        self.preview.setStyleSheet("color: #0a84ff; font-weight: 600;")
        layout.addWidget(self.preview)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        self.ok_btn = QPushButton("确定")
        self.ok_btn.setProperty("primary", True)
        self.ok_btn.clicked.connect(self._confirm)
        btn_row.addWidget(self.ok_btn)
        layout.addLayout(btn_row)

        self.zone_combo.currentTextChanged.connect(self._on_zone_changed)
        self.existing_combo.currentTextChanged.connect(self._update_preview)
        self.sub_edit.textChanged.connect(self._update_preview)
        self._on_zone_changed()

    def _on_zone_changed(self) -> None:
        """主域名变化时，拉取该 zone 下所有 A 记录域名填入现有域名下拉框。"""
        zone = self.zone_combo.currentText().strip().rstrip(".")
        token = config.get("cf_token", "")
        self.existing_combo.clear()
        self.existing_combo.addItem("（新建，用下方二级域名）", "")
        if not zone or not token:
            self._update_preview()
            return
        try:
            zid = cloudflare.get_zone_id(token, zone)
            recs = cloudflare.list_dns_records(token, zid)
            seen = set()
            for rec in recs:
                name = rec.get("name", "")
                if name and name != zone and name.endswith("." + zone) and name not in seen:
                    seen.add(name)
                    self.existing_combo.addItem(name, name)
        except Exception:
            pass
        self._update_preview()

    def _update_preview(self) -> None:
        domain = self.entry_domain()
        if domain:
            self.preview.setText(f"入口域名：{domain}")
        else:
            self.preview.setText("")

    def _confirm(self) -> None:
        if not self.entry_domain():
            self.sub_edit.setFocus()
            return
        self.accept()

    def entry_domain(self) -> str:
        # 优先用现有域名下拉框选的值
        existing = self.existing_combo.currentData()
        if existing:
            return existing
        # 否则用二级域名 + 主域名
        zone = self.zone_combo.currentText().strip().rstrip(".")
        sub = self.sub_edit.text().strip().strip(".")
        if not sub or not zone:
            return ""
        return f"{sub}.{zone}"


class WebsharePage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._workers: list[CallWorker] = []
        self._servers: list[tuple[str, str, str]] = []  # [(id, ip, name)]
        self._dc: list[dict] = []
        self._res: list[dict] = []
        self._plan_bw: dict[int, float] = {}
        self._plan_repl: dict[int, int] = {}  # plan_id -> 剩余替换次数
        self._page: dict[str, int] = {"dc": 0, "res": 0}  # 当前页码（0-based）
        self._page_widgets: dict[str, dict] = {}  # ip_type -> 分页控件
        self._selected: dict[str, tuple] = {}  # 勾选的 IP：ip_key -> (ip_type, orig_i)
        self._relay_map: dict[str, dict] = {}  # ip_key -> relay（判断是否中转 + 取消中转）
        self._server_ip_map: dict[str, tuple[str, str]] = {}  # server_id -> (ip, name)
        self._suppress = False
        self._ipqs_credits: int | None = None  # 账户真实剩余 credits（account API 查询）
        self._quota_timer = QTimer(self)
        self._quota_timer.setSingleShot(True)
        self._quota_timer.timeout.connect(self._probe_quota)
        # 每 10 分钟后台刷新一次订阅流量（webshare /stats/ 按小时聚合，10 分钟兜底已足够；列表在切页/手动刷新时拉全量）
        self._sub_timer = QTimer(self)
        self._sub_timer.setInterval(10 * 60 * 1000)
        self._sub_timer.timeout.connect(self._refresh_subscription)
        self._sub_timer.start()
        self._build()
        self._load_servers()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(12)

        # 标题行：标题 + 刷新列表
        title_row = QHBoxLayout()
        title = QLabel("🛜 Webshare-IP")
        title.setStyleSheet("font-size: 20px; font-weight: 700;")
        title_row.addWidget(title)
        title_row.addStretch(1)
        self.refresh_btn = QPushButton("刷新列表")
        self.refresh_btn.clicked.connect(self.refresh)
        title_row.addWidget(self.refresh_btn)
        root.addLayout(title_row)

        # 状态（加载状态）
        self.status = QLabel("")
        root.addWidget(self.status)

        # 前置服务器（中转节点部署到哪台）
        srv_card = QFrame()
        srv_card.setProperty("card", True)
        sc = QVBoxLayout(srv_card)
        sc.setContentsMargins(20, 12, 20, 12)
        sc.setSpacing(8)
        srv_row = QHBoxLayout()
        srv_row.setSpacing(8)
        ssec = QLabel("前置服务器")
        ssec.setProperty("sectionTitle", True)
        srv_row.addWidget(ssec)
        self.server_combo = QComboBox()
        self.server_combo.setMinimumWidth(280)
        srv_row.addWidget(self.server_combo, 1)
        self.batch_gen_btn = QPushButton("批量生成中转")
        self.batch_gen_btn.setProperty("primary", True)
        self.batch_gen_btn.clicked.connect(self._generate)
        srv_row.addWidget(self.batch_gen_btn)
        self.srv_refresh_btn = QPushButton("刷新服务器")
        self.srv_refresh_btn.clicked.connect(self._load_servers)
        srv_row.addWidget(self.srv_refresh_btn)
        self.apply_btn = QPushButton("重新部署")
        self.apply_btn.clicked.connect(self._apply)
        srv_row.addWidget(self.apply_btn)
        sc.addLayout(srv_row)
        self.stats_label = QLabel("")
        self.stats_label.setStyleSheet("color: #8e8e93;")
        sc.addWidget(self.stats_label)
        self.plans_label = QLabel("")
        self.plans_label.setStyleSheet("color: #8e8e93;")
        self.plans_label.setWordWrap(True)
        sc.addWidget(self.plans_label)
        root.addWidget(srv_card)

        # 筛选/批量选择栏（国家 / 城市 / 数量 / 方式 / 自动勾选 / 清除勾选）
        filter_row = QHBoxLayout()
        filter_row.setSpacing(8)
        filter_row.addWidget(QLabel("筛选"))
        self.filter_country = QComboBox()
        self.filter_country.addItem("全部国家", "")
        self.filter_country.setMinimumWidth(110)
        self.filter_country.currentIndexChanged.connect(self._on_country_filter_changed)
        filter_row.addWidget(self.filter_country)
        self.filter_city = QComboBox()
        self.filter_city.addItem("全部城市", "")
        self.filter_city.setMinimumWidth(130)
        self.filter_city.currentIndexChanged.connect(self._refill_tables)
        filter_row.addWidget(self.filter_city)
        filter_row.addWidget(QLabel("选"))
        self.filter_count = QSpinBox()
        self.filter_count.setRange(1, 1000)
        self.filter_count.setValue(1)
        self.filter_count.setFixedWidth(70)
        filter_row.addWidget(self.filter_count)
        self.filter_mode = QComboBox()
        self.filter_mode.addItem("按顺序", "seq")
        self.filter_mode.addItem("随机", "random")
        self.filter_mode.setFixedWidth(90)
        filter_row.addWidget(self.filter_mode)
        self.auto_check_btn = QPushButton("自动勾选")
        self.auto_check_btn.setProperty("primary", True)
        self.auto_check_btn.clicked.connect(self._auto_check)
        filter_row.addWidget(self.auto_check_btn)
        self.clear_check_btn = QPushButton("清除勾选")
        self.clear_check_btn.clicked.connect(self._clear_check)
        filter_row.addWidget(self.clear_check_btn)
        self.selected_label = QLabel("")
        self.selected_label.setStyleSheet("color: #0a84ff;")
        filter_row.addWidget(self.selected_label, 1)
        root.addLayout(filter_row)

        # 标签页：代理服务器 / 静态住宅
        self.tabs = QTabWidget()
        self.dc_table = self._make_table("dc")
        self.res_table = self._make_table("res")
        self.tabs.addTab(self._wrap(self.dc_table, "dc"), "代理服务器（机房）")
        self.tabs.addTab(self._wrap(self.res_table, "res"), "静态住宅（ISP）")
        root.addWidget(self.tabs, 1)

    def _make_table(self, ip_type: str) -> DragTableWidget:
        table = DragTableWidget(0, len(_COLS))
        table.setHorizontalHeaderLabels(_COLS)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setAlternatingRowColors(True)
        table.verticalHeader().setDefaultSectionSize(52)  # 行高 52，按钮完整显示
        table.verticalHeader().setVisible(False)
        hdr = table.horizontalHeader()
        hdr.setStretchLastSection(True)
        hdr.setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        # 内容列 Interactive（自由拖列宽 + 持久化），操作列 stretch last（填剩余，大屏无横滚）
        for c in range(_OP_COL):
            hdr.setSectionResizeMode(c, QHeaderView.ResizeMode.Interactive)
        for c, w in _COL_DEFAULTS.items():
            table.setColumnWidth(c, w)
        col_key = f"webshare_{ip_type}_col_widths"
        table._suppress_resize = True
        saved = config.get(col_key, {})
        items = []
        for c, w in saved.items():
            try:
                c = int(c)
                if c < _OP_COL and 0 < int(w) < 600:
                    items.append((c, int(w)))
            except (ValueError, IndexError):
                pass
        if sum(w for _, w in items) <= 2000:
            for c, w in items:
                table.setColumnWidth(c, w)
        table._suppress_resize = False
        hdr.sectionResized.connect(lambda col, old, new, k=col_key, t=table: self._on_col_resized(k, t, col, new))
        table.row_dropped.connect(lambda s, d, t=ip_type: self._on_row_dropped(t, s, d))
        table.itemChanged.connect(self._on_item_changed)
        table.cellDoubleClicked.connect(self._on_cell_dblclick)
        return table

    def _wrap(self, table: DragTableWidget, ip_type: str) -> QWidget:
        w = QWidget()
        l = QVBoxLayout(w)
        l.setContentsMargins(0, 12, 0, 0)
        l.addWidget(table)
        # 分页栏：上一页 / 页码 / 下一页 / 跳转
        pager = QWidget()
        pl = QHBoxLayout(pager)
        pl.setContentsMargins(0, 8, 0, 4)
        pl.setSpacing(6)
        pl.addStretch(1)
        prev_btn = QPushButton("上一页")
        prev_btn.setProperty("compact", True)
        prev_btn.clicked.connect(lambda _=False, t=ip_type: self._goto_page(t, self._page.get(t, 0) - 1))
        pl.addWidget(prev_btn)
        page_label = QLabel("")
        page_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        page_label.setMinimumWidth(110)
        pl.addWidget(page_label)
        next_btn = QPushButton("下一页")
        next_btn.setProperty("compact", True)
        next_btn.clicked.connect(lambda _=False, t=ip_type: self._goto_page(t, self._page.get(t, 0) + 1))
        pl.addWidget(next_btn)
        page_input = QLineEdit()
        page_input.setPlaceholderText("页码")
        page_input.setFixedWidth(56)
        page_input.setAlignment(Qt.AlignmentFlag.AlignCenter)
        page_input.returnPressed.connect(lambda t=ip_type, inp=page_input: self._jump_to_page(t, inp))
        pl.addWidget(page_input)
        go_btn = QPushButton("跳转")
        go_btn.setProperty("compact", True)
        go_btn.clicked.connect(lambda _=False, t=ip_type, inp=page_input: self._jump_to_page(t, inp))
        pl.addWidget(go_btn)
        pl.addStretch(1)
        l.addWidget(pager)
        self._page_widgets[ip_type] = {"prev": prev_btn, "next": next_btn, "label": page_label, "input": page_input}
        return w

    def activate(self) -> None:
        if config.get("webshare_api_key", ""):
            self.refresh()
        if not self.server_combo.count():
            self._load_servers()

    # ---------- 前置服务器 ----------

    def _load_servers(self) -> None:
        cur = self.server_combo.currentData() if self.server_combo.count() else None
        self.server_combo.clear()
        self._servers = []
        key = config.get("vultr_api_key", "")
        if key:
            try:
                from ..vultr import VultrAPI

                api = VultrAPI(key)
                for inst in api.list_instances():
                    if inst.get("power_status") != "running":
                        continue
                    ip = inst.get("main_ip", "")
                    if self._is_cgnat(ip):
                        continue
                    custom = config.get("server_names", {}).get(inst["id"], "")
                    name = custom or inst.get("label", inst["id"])
                    self._servers.append((inst["id"], ip, name))
            except Exception as e:
                self._set_status(f"加载 Vultr 服务器失败：{e}", warn=True)
        for m in config.get("manual_servers", []):
            self._servers.append((f"manual:{m['id']}", m.get("host", ""), m.get("name", "手动节点")))
        self._server_ip_map = {sid: (ip, name) for sid, ip, name in self._servers}
        # 每台服务器的中转数
        relay_count: dict[str, int] = {}
        for r in config.get("relays", []):
            sid = r.get("server_id", "")
            relay_count[sid] = relay_count.get(sid, 0) + 1
        for sid, ip, name in self._servers:
            n = relay_count.get(sid, 0)
            label = f"{name}（{ip}）· 中转 {n} 个" if n else f"{name}（{ip}）"
            self.server_combo.addItem(label, sid)
        if cur is not None:
            idx = self.server_combo.findData(cur)
            if idx >= 0:
                self.server_combo.setCurrentIndex(idx)
        elif relay_count:
            # 首次加载：默认选中转发最多的服务器
            best_sid = max(relay_count, key=relay_count.get)
            idx = self.server_combo.findData(best_sid)
            if idx >= 0:
                self.server_combo.setCurrentIndex(idx)
        if not self._servers:
            self._set_status("没有可用的前置服务器（需先建 Vultr 实例或手动添加）", warn=True)

    @staticmethod
    def _is_cgnat(ip: str) -> bool:
        parts = ip.split(".")
        if len(parts) != 4:
            return False
        try:
            a = int(parts[0])
            b = int(parts[1])
        except ValueError:
            return False
        return a == 100 and 64 <= b <= 127

    # ---------- webshare 列表 ----------

    @staticmethod
    def _ip_key(p: dict) -> str:
        return f"{p.get('proxy_address') or 'p.webshare.io'}:{p.get('port')}"

    def _sort_proxies(self, ip_type: str) -> None:
        order = config.get(f"webshare_{ip_type}_order", []) or []
        if not order:
            return
        order_map = {k: i for i, k in enumerate(order)}
        official = getattr(self, "_official_order", {}).get(ip_type, {})
        pool = self._dc if ip_type == "dc" else self._res
        # 拖拽过的按拖拽位置；取消中转回来的（不在 order）按官网顺序插回
        pool.sort(key=lambda p: order_map.get(self._ip_key(p), official.get(self._ip_key(p), len(order) + 99999)))

    def refresh(self) -> None:
        key = config.get("webshare_api_key", "")
        if not key:
            self._set_status("请先在「设置」填写 webshare API 密钥", warn=True)
            return
        self._set_status("加载中…", loading=True)
        self.refresh_btn.setEnabled(False)

        def _do():
            return webshare.load_lists(key)

        w = CallWorker(_do)
        w.done.connect(self._on_loaded)
        w.failed.connect(self._on_failed)
        self._workers.append(w)
        w.finished.connect(w.deleteLater)
        w.start()

    def _on_loaded(self, data: dict) -> None:
        self.refresh_btn.setEnabled(True)
        self._dc = data.get("proxy_servers", [])
        self._res = data.get("static_residential", [])
        self._plan_bw = {p["id"]: p.get("bandwidth_limit", 0) for p in data.get("plans", [])}
        self._plan_repl = {p["id"]: p.get("proxy_replacements_available", 0) for p in data.get("plans", [])}
        # 持久化套餐 + 订阅信息（中转节点页「分配给用户」弹窗读，显示订阅到期/流量池）
        config.set_value("webshare_plans", data.get("plans", []))
        config.set_value("webshare_subscription", data.get("subscription", {}))
        # relay 匹配表：ip_key -> relay（判断是否中转 / 取消中转）
        self._relay_map = {f"{r.get('host','')}:{r.get('port','')}": r for r in config.get("relays", [])}
        self._recycled_map = {f"{r.get('host','')}:{r.get('port','')}": r for r in config.get("recycled_relays", []) or []}
        self._server_ip_map = {sid: (ip, name) for sid, ip, name in self._servers}
        # 官网返回顺序（取消中转的 IP 按此插回，不套用拖拽顺序）
        self._official_order = {
            "dc": {self._ip_key(p): i for i, p in enumerate(self._dc)},
            "res": {self._ip_key(p): i for i, p in enumerate(self._res)},
        }
        self._sort_proxies("dc")
        self._sort_proxies("res")
        # 填充国家筛选列表（只列未中转 IP 出现的国家）
        countries = sorted({p.get("country_code", "") for p in self._dc + self._res
                            if p.get("country_code") and self._ip_key(p) not in self._relay_map
                            and self._ip_key(p) not in self._recycled_map})
        self.filter_country.blockSignals(True)
        self.filter_country.clear()
        self.filter_country.addItem("全部国家", "")
        for c in countries:
            self.filter_country.addItem(c, c)
        self.filter_country.blockSignals(False)
        # 初始填充城市列表 + 重新渲染
        self._on_country_filter_changed()
        self._update_plans(data.get("plans", []), data.get("subscription", {}))
        self._update_relay_overview()
        self.tabs.setTabText(0, f"代理服务器（机房 · {len(self._dc)}）")
        self.tabs.setTabText(1, f"静态住宅（ISP · {len(self._res)}）")
        self._set_status(
            f"加载成功：代理服务器 {len(self._dc)} 个，静态住宅 {len(self._res)} 个", ok=True
        )
        # 异步查询 IPQS 账户真实剩余 credits（按钮显示真实次数）
        self._refresh_ipqs_credits()

    _PLAN_NAMES = {
        "default": "机房", "isp": "静态住宅", "residential": "轮换住宅",
        "premium": "高级机房", "datacenter_and_isp": "机房+住宅",
        "semidedicated": "半独享", "dedicated": "独享",
    }

    def _update_plans(self, plans: list, subscription: dict) -> None:
        if not plans:
            self.plans_label.setText("")
            return
        parts = []
        for p in plans:
            name = self._PLAN_NAMES.get(p.get("subtype", ""), p.get("subtype", "其他"))
            bw = p.get("bandwidth_limit", 0)
            used = p.get("bandwidth_used", 0)
            price = p.get("monthly_price", 0)
            cnt = p.get("proxy_count", 0)
            if bw:
                parts.append(f"{name} {cnt} IP · {bw:g}GB/月 · 已用 {used:.1f}GB · ${price:.2f}/月")
            else:
                parts.append(f"{name} {cnt} IP · 无限流量 · 已用 {used:.1f}GB · ${price:.2f}/月")
        end_date = (subscription.get("end_date") or "")[:10]
        head = "📦 套餐"
        if end_date:
            head += f"（到期 {end_date}）"
        self.plans_label.setText(head + "　" + "　|　".join(parts))

    def _refresh_subscription(self) -> None:
        """每分钟刷新订阅流量（plans + stats），更新 config 供「分配用户」弹窗读最新剩余。"""
        api_key = str(config.get("webshare_api_key", "") or "")
        if not api_key:
            return
        from ..webshare import load_subscription

        def _do():
            return load_subscription(api_key)

        w = CallWorker(_do)
        w.done.connect(self._on_subscription_refreshed)
        w.failed.connect(lambda _m: None)  # 静默失败，下个周期再试
        self._workers.append(w)
        w.finished.connect(w.deleteLater)
        w.start()

    def _on_subscription_refreshed(self, data) -> None:
        config.set_value("webshare_plans", data.get("plans", []))
        config.set_value("webshare_subscription", data.get("subscription", {}))
        self._update_plans(data.get("plans", []), data.get("subscription", {}))

    def _update_relay_overview(self) -> None:
        """更新顶部统计（已中转 / 冷却 / 回归 / 未中转）。"""
        relays = config.get("relays", [])
        recycled = config.get("recycled_relays", []) or []
        returned = config.get("returned_ips", {}) or {}
        total_ip = len(self._dc) + len(self._res)
        relayed = len({f"{r.get('host','')}:{r.get('port','')}" for r in relays})
        recycled_cnt = len({f"{r.get('host','')}:{r.get('port','')}" for r in recycled})
        returned_cnt = len(returned)
        unrelayed = total_ip - relayed - recycled_cnt
        if total_ip:
            self.stats_label.setText(
                f'已中转 <span style="color:#9db8ff">{relayed}</span> · '
                f'冷却 <span style="color:#ff9f0a">{recycled_cnt}</span> · '
                f'回归 <span style="color:#f5c542">{returned_cnt}</span> · '
                f'未中转 <span style="color:#32d74b">{unrelayed}</span>（含回归）'
            )
        else:
            self.stats_label.setText("")

    def _rename_server(self, srv_id: str) -> None:
        """改名服务器：弹窗输入新名，保存 config.server_names + 同步 Vultr label。"""
        from PySide6.QtWidgets import QInputDialog

        cur_ip, cur_name = self._server_ip_map.get(srv_id, ("", ""))
        new_name, ok = QInputDialog.getText(self, "改服务器名称", "新名称：", text=cur_name)
        if not ok or not new_name.strip():
            return
        new_name = new_name.strip()
        # 保存自定义名（本地显示）
        names = dict(config.get("server_names", {}))
        names[srv_id] = new_name
        config.set_value("server_names", names)
        self._server_ip_map[srv_id] = (cur_ip, new_name)
        self._update_relay_overview()
        self._load_servers()
        # 同步到 Vultr（改 label，异步）
        key = config.get("vultr_api_key", "")
        if not key or srv_id.startswith("manual:"):
            self._set_status(f"已改名「{new_name}」", ok=True)
            return

        def _do():
            from ..vultr import VultrAPI

            VultrAPI(key).rename_instance(srv_id, new_name)
            return new_name

        w = CallWorker(_do)
        w.done.connect(lambda n: self._set_status(f"已改名「{n}」并同步到 Vultr", ok=True))
        w.failed.connect(lambda m: self._set_status(f"已改名，但同步 Vultr 失败：{m}", warn=True))
        self._workers.append(w)
        w.finished.connect(w.deleteLater)
        w.start()

    def _on_failed(self, msg: str) -> None:
        self.refresh_btn.setEnabled(True)
        self._set_status(f"失败：{msg}", warn=True)

    def _fill(self, table: DragTableWidget, proxies: list, ip_type: str) -> None:
        # 只显示未中转 + 符合国家/城市筛选的 IP；回归过的排最后
        country = self.filter_country.currentData()
        city = self.filter_city.currentData()
        returned_ips = config.get("returned_ips", {}) or {}
        pairs = [(i, p) for i, p in enumerate(proxies)
                 if self._ip_key(p) not in self._relay_map
                 and self._ip_key(p) not in self._recycled_map
                 and (not country or p.get("country_code", "") == country)
                 and (not city or p.get("city_name", "") == city)]
        pairs.sort(key=lambda x: self._ip_key(x[1]) in returned_ips)
        # 分页：每页 _PAGE_SIZE 个
        total = len(pairs)
        total_pages = max(1, (total + _PAGE_SIZE - 1) // _PAGE_SIZE)
        page = max(0, min(self._page.get(ip_type, 0), total_pages - 1))
        self._page[ip_type] = page
        start = page * _PAGE_SIZE
        page_pairs = pairs[start:start + _PAGE_SIZE]
        notes = config.get("webshare_notes", {})
        scores = config.get("webshare_scores", {})
        self._suppress = True
        table.setRowCount(0)
        table.setRowCount(len(page_pairs))
        backbone = ip_type == "res"
        for row, (orig_i, p) in enumerate(page_pairs):
            ip_key = self._ip_key(p)
            # 选择（checkbox）：勾选状态持久在 self._selected
            chk = QTableWidgetItem()
            chk.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            chk.setCheckState(Qt.CheckState.Checked if ip_key in self._selected else Qt.CheckState.Unchecked)
            chk.setData(Qt.ItemDataRole.UserRole, (ip_type, orig_i))
            table.setItem(row, 0, chk)

            addr = p.get("proxy_address") or ("p.webshare.io" if backbone else "")
            port = str(p.get("port", ""))
            country = p.get("country_code", "")
            city = p.get("city_name", "")
            plan_bw = self._plan_bw.get(p.get("_plan_id"))
            plan_text = (f"{plan_bw:g}GB" if plan_bw else "无限") if plan_bw is not None else "-"
            valid = "✅" if p.get("valid") else "❌"

            self._item(table, row, 1, str(row + 1))  # 序号
            self._item(table, row, 2, addr)
            self._item(table, row, 3, port)
            self._item(table, row, 4, _cn_country(country))  # 国家中文
            self._item(table, row, 5, _cn_city(city))  # 城市中文
            self._item(table, row, 6, plan_text)
            self._item(table, row, 7, valid)
            # 评分（IPQS fraud_score，持久化）
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
                score_item.setToolTip(
                    f"fraud_score={score_val}  proxy={score_rec.get('proxy')}  vpn={score_rec.get('vpn')}  "
                    f"tor={score_rec.get('tor')}  恶迹={score_rec.get('abuse')}"
                )
            else:
                score_item = QTableWidgetItem("-")
                score_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            table.setItem(row, 9, score_item)
            # 标记列：回收过的 IP 显示「回归」徽章（没回收过留空）
            if ip_key in returned_ips:
                badge = QLabel("回归")
                badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
                badge.setStyleSheet("background:#f5c542; color:#1a1a1a; border-radius:9px; padding:2px 6px; font-size:11px; font-weight:600;")
                badge.setToolTip(f"回归时间：{returned_ips.get(ip_key, '')}（曾回收，注意可能存在问题）")
                table.setCellWidget(row, 8, badge)
            else:
                self._item(table, row, 8, "")
            # 备注（可编辑，持久化）
            note_item = QTableWidgetItem(notes.get(ip_key, ""))
            note_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            note_item.setData(Qt.ItemDataRole.UserRole, ip_key)
            note_item.setToolTip("双击编辑备注（用户名/密码见 tooltip）")
            table.setItem(row, 10, note_item)

            # 操作列：生成中转 + 检测 IP + 替换 IP
            w = QWidget()
            lay = QHBoxLayout(w)
            lay.setContentsMargins(4, 2, 4, 2)
            lay.addStretch(1)
            gen_btn = QPushButton("生成中转")
            gen_btn.setProperty("compact", True)
            gen_btn.setProperty("primary", True)
            gen_btn.setFixedHeight(30)
            gen_btn.setToolTip("为该 IP 生成中转节点并部署到前置服务器")
            gen_btn.clicked.connect(lambda _=False, t=ip_type, i=orig_i: self._generate_one(t, i))
            lay.addWidget(gen_btn)
            rem = self._ipqs_remaining()
            check_btn = QPushButton(f"检测(剩{rem})")
            check_btn.setObjectName("check_btn")
            check_btn.setProperty("compact", True)
            check_btn.setFixedHeight(30)
            tip = "IPQS 检测 IP 质量（fraud_score），每日限次"
            if self._ipqs_credits is not None:
                tip += f"\n本月剩余 {self._ipqs_credits} 次"
            check_btn.setToolTip(tip)
            check_btn.setEnabled(rem > 0)
            if rem <= 0:
                self._start_quota_timer()
            check_btn.clicked.connect(lambda _=False, b=check_btn, a=addr, k=ip_key: self._check_ip(a, k, b))
            lay.addWidget(check_btn)
            # 替换按钮（只对未中转 IP；已中转替换会导致 relay 失效）
            plan_id = p.get("_plan_id")
            repl_avail = self._plan_repl.get(plan_id, 0)
            repl_btn = QPushButton(f"替换(剩{repl_avail})")
            repl_btn.setProperty("compact", True)
            repl_btn.setFixedHeight(30)
            repl_btn.setToolTip("替换成新的同类型 IP（消耗月度替换配额）")
            repl_btn.setEnabled(repl_avail > 0)
            repl_btn.clicked.connect(lambda _=False, b=repl_btn, a=addr, pid=plan_id, cc=p.get("country_code", ""): self._replace_ip(a, pid, cc, b))
            lay.addWidget(repl_btn)
            lay.addStretch(1)
            table.setCellWidget(row, _OP_COL, w)
        self._suppress = False
        self._update_pager(ip_type, total, page, total_pages)

    def _open_batch_dialog(self) -> None:
        """打开批量生成中转对话框：选国家/城市/数量/方式。"""
        countries = set()
        country_cities: dict[str, set] = {}
        for p in self._dc + self._res:
            if self._ip_key(p) in self._relay_map:
                continue
            cc = p.get("country_code", "")
            city = p.get("city_name", "")
            if cc:
                countries.add(cc)
                country_cities.setdefault(cc, set())
                if city:
                    country_cities[cc].add(city)
        if not countries:
            self._set_status("没有可生成的未中转 IP", warn=True)
            return
        dlg = BatchGenerateDialog(sorted(countries), country_cities, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        country = dlg.country_combo.currentData()
        city = dlg.city_combo.currentData()
        count = dlg.count_spin.value()
        mode = dlg.mode_combo.currentData()
        # 收集符合条件的未中转 IP
        candidates = []
        for ip_type, pool in (("dc", self._dc), ("res", self._res)):
            for i, p in enumerate(pool):
                if self._ip_key(p) in self._relay_map:
                    continue
                if country and p.get("country_code", "") != country:
                    continue
                if city and p.get("city_name", "") != city:
                    continue
                candidates.append((ip_type, i, p))
        if not candidates:
            self._set_status("没有符合条件的 IP", warn=True)
            return
        if mode == "random":
            import random
            random.shuffle(candidates)
        selected = candidates[:count]
        # 弹域名框
        d2 = DomainDialog(self)
        if d2.exec() != QDialog.DialogCode.Accepted:
            return
        entry_domain = d2.entry_domain()
        if not entry_domain:
            self._set_status("未填写二级域名", warn=True)
            return
        server_id = self.server_combo.currentData()
        if not server_id:
            self._set_status("请先选择前置服务器", warn=True)
            return
        checked = [(it, idx) for it, idx, _ in selected]
        self._generate_relays(server_id, entry_domain, checked)

    def _on_country_filter_changed(self, *_args) -> None:
        """国家筛选变化：更新城市下拉框 + 重新渲染。"""
        country = self.filter_country.currentData()
        cities = set()
        for p in self._dc + self._res:
            if self._ip_key(p) in self._relay_map:
                continue
            if country and p.get("country_code", "") != country:
                continue
            if p.get("city_name"):
                cities.add(p.get("city_name"))
        self.filter_city.blockSignals(True)
        self.filter_city.clear()
        self.filter_city.addItem("全部城市", "")
        for c in sorted(cities):
            self.filter_city.addItem(c, c)
        self.filter_city.blockSignals(False)
        self._refill_tables()

    def _refill_tables(self, *_args) -> None:
        """重新渲染两个标签页（重置到第 1 页）。"""
        self._page["dc"] = 0
        self._page["res"] = 0
        self._fill(self.dc_table, self._dc, "dc")
        self._fill(self.res_table, self._res, "res")
        self._update_selected_label()

    def _auto_check(self) -> None:
        """自动勾选：从当前标签页 + 国家/城市筛选结果里选 N 个（随机或顺序）。"""
        ip_type = "dc" if self.tabs.currentIndex() == 0 else "res"
        pool = self._dc if ip_type == "dc" else self._res
        country = self.filter_country.currentData()
        city = self.filter_city.currentData()
        count = self.filter_count.value()
        mode = self.filter_mode.currentData()
        candidates = []
        for i, p in enumerate(pool):
            if self._ip_key(p) in self._relay_map:
                continue
            if country and p.get("country_code", "") != country:
                continue
            if city and p.get("city_name", "") != city:
                continue
            candidates.append((ip_type, i, self._ip_key(p)))
        if mode == "random":
            import random
            random.shuffle(candidates)
        selected = candidates[:count]
        self._selected = {ip_key: (it, i) for it, i, ip_key in selected}
        self._refill_tables()

    def _clear_check(self) -> None:
        self._selected = {}
        self._refill_tables()

    def _update_selected_label(self) -> None:
        n = len(self._selected)
        self.selected_label.setText(f"已勾选 {n} 个 IP" if n else "")

    def _goto_page(self, ip_type: str, page: int) -> None:
        """翻页（page 0-based，自动夹到合法范围）。"""
        table = self.dc_table if ip_type == "dc" else self.res_table
        pool = self._dc if ip_type == "dc" else self._res
        self._page[ip_type] = page
        self._fill(table, pool, ip_type)

    def _jump_to_page(self, ip_type: str, inp: QLineEdit) -> None:
        """输入页码跳转（1-based）。"""
        try:
            page = int(inp.text().strip()) - 1
        except ValueError:
            return
        self._goto_page(ip_type, page)

    def _update_pager(self, ip_type: str, total: int, page: int, total_pages: int) -> None:
        """更新分页栏：按钮可用状态 + 页码显示。"""
        w = self._page_widgets.get(ip_type)
        if not w:
            return
        w["prev"].setEnabled(page > 0)
        w["next"].setEnabled(page < total_pages - 1)
        w["label"].setText(f"第 {page + 1} / {total_pages} 页 · 共 {total} 个")

    def _ipqs_remaining(self) -> int:
        """今天剩余检测次数（日限额 35，本地计数，跨北京时间 0 点重置）。"""
        return ipqs.ipqs_remaining()

    def _refresh_ipqs_credits(self) -> None:
        """异步查询 IPQS 账户真实剩余 credits，刷新按钮显示。"""
        ipqs_key = config.get("ipqs_key") or "fqjGYzhdyEYZS5YqE7KcFtUqJwXJC75W"

        def _do():
            url = f"https://www.ipqualityscore.com/api/json/account/{ipqs_key}"
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=15) as resp:
                r = json.load(resp)
            if r.get("success"):
                return r.get("credits", 0)
            return None

        w = CallWorker(_do)
        w.done.connect(self._on_credits_loaded)
        w.failed.connect(lambda m: self._on_credits_loaded(None))
        self._workers.append(w)
        w.finished.connect(w.deleteLater)
        w.start()

    def _on_credits_loaded(self, credits) -> None:
        if credits is not None:
            self._ipqs_credits = int(credits)
        # 刷新按钮（按钮显示日限额剩余；月剩余存 self._ipqs_credits 供 tooltip 参考）
        self._fill(self.dc_table, self._dc, "dc")
        self._fill(self.res_table, self._res, "res")

    def _bump_ipqs_usage(self) -> None:
        ipqs.bump_ipqs_usage()

    def _start_quota_timer(self) -> None:
        """额度耗尽后启动 24 小时定时器，到点自动探测是否恢复。"""
        if not self._quota_timer.isActive():
            self._quota_timer.start(24 * 3600 * 1000)  # 24 小时

    def _probe_quota(self) -> None:
        """探测 IPQS 额度是否已恢复（查一个固定 IP，看是否 success）。"""
        ipqs_key = config.get("ipqs_key") or "fqjGYzhdyEYZS5YqE7KcFtUqJwXJC75W"

        def _do():
            url = f"https://www.ipqualityscore.com/api/json/ip/{ipqs_key}/8.8.8.8"
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=15) as resp:
                r = json.load(resp)
            return r.get("success", False)

        w = CallWorker(_do)
        w.done.connect(self._on_probe_done)
        w.failed.connect(lambda m: self._on_probe_done(False))
        self._workers.append(w)
        w.finished.connect(w.deleteLater)
        w.start()

    def _on_probe_done(self, recovered: bool) -> None:
        if recovered:
            # 额度已恢复：重新查询真实剩余 credits（account API 返回真实值）
            self._refresh_ipqs_credits()
            self._set_status("IPQS 额度已恢复，已重新查询剩余次数", ok=True)
        else:
            # 还没恢复：继续禁用，重新启动 24 小时定时器
            self._start_quota_timer()

    def _check_ip(self, addr: str, ip_key: str, btn: QPushButton) -> None:
        """IPQS 检测 IP 质量（fraud_score），结果存库并刷新列表。"""
        if not re.match(r"^\d{1,3}(\.\d{1,3}){3}$", addr):
            btn.setText("域名")
            btn.setToolTip("骨干域名（p.webshare.io）无法检测具体 IP")
            return
        if self._ipqs_remaining() <= 0:
            btn.setEnabled(False)
            return
        btn.setEnabled(False)
        btn.setText("检测中…")

        def _do():
            r = ipqs.check_ip(addr)
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
            # 每日额度耗尽：禁用按钮 + 启动 24 小时定时器自动探测恢复
            limit = int(config.get("ipqs_daily_limit", 35))
            config.set_value("ipqs_usage", {"date": _bj_today(), "count": limit})
            btn.setText("超额")
            btn.setToolTip(f"IPQS 每日额度已用完：{data}\n24 小时后自动探测恢复")
            self._start_quota_timer()
            self._fill(self.dc_table, self._dc, "dc")
            self._fill(self.res_table, self._res, "res")
            return
        score, proxy, vpn, tor, abuse = data
        # 存分数到 config（持久化，重新打开软件不丢），带检测时间
        scores = config.get("webshare_scores", {})
        scores[ip_key] = {"score": score, "proxy": proxy, "vpn": vpn, "tor": tor,
                          "abuse": abuse, "ts": time.time(),
                          "time_str": time.strftime("%Y-%m-%d %H:%M:%S")}
        config.set_value("webshare_scores", scores)
        # 已用次数 +1（本地计数）
        self._bump_ipqs_usage()
        # 真实 credits 递减（如果有缓存）
        if self._ipqs_credits is not None:
            self._ipqs_credits = max(0, self._ipqs_credits - 1)
        # 刷新两个标签页（评分列显示新分数 + 检测按钮剩余次数更新）
        self._fill(self.dc_table, self._dc, "dc")
        self._fill(self.res_table, self._res, "res")

    def _replace_ip(self, addr: str, plan_id, country_code: str, btn: QPushButton) -> None:
        """替换 IP（异步：提交替换请求 → 轮询到完成 → 刷新列表）。"""
        btn.setEnabled(False)
        btn.setText("替换中…")
        api_key = config.get("webshare_api_key", "")

        def _do():
            result = webshare.replace_proxy(api_key, plan_id, addr, country_code)
            repl_id = result.get("id")
            if not repl_id:
                return ("fail", result.get("error") or result.get("detail") or "替换请求失败")
            # 轮询状态直到 completed / failed（最多约 30 秒）
            for _ in range(15):
                time.sleep(2)
                status = webshare.get_replacement_status(api_key, plan_id, repl_id)
                state = status.get("state", "")
                if state == "completed":
                    return ("ok", status.get("proxies_added", 1))
                if state == "failed":
                    return ("fail", status.get("error") or "替换失败")
            return ("fail", "替换超时（可能仍在处理，稍后刷新列表查看）")

        w = CallWorker(_do)
        w.done.connect(self._on_replaced)
        w.failed.connect(lambda m: self._on_replaced(("fail", str(m))))
        self._workers.append(w)
        w.finished.connect(w.deleteLater)
        w.start()

    def _on_replaced(self, res) -> None:
        if res and res[0] == "ok":
            self._set_status(f"替换成功：新 IP 已生效", ok=True)
            self.refresh()  # 刷新列表，显示新 IP
        else:
            self._set_status(f"替换失败：{res[1] if res else '未知错误'}", warn=True)
            self._fill(self.dc_table, self._dc, "dc")
            self._fill(self.res_table, self._res, "res")

    def _item(self, table: DragTableWidget, row: int, col: int, text: str) -> None:
        it = QTableWidgetItem(str(text))
        it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        table.setItem(row, col, it)

    # ---------- 备注编辑 ----------

    def _on_cell_dblclick(self, row: int, col: int) -> None:
        if col != _NOTE_COL:
            return
        table = self.sender() if isinstance(self.sender(), DragTableWidget) else None
        if table is None:
            return
        table.editItem(table.item(row, col))

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if self._suppress:
            return
        if item.column() == 0:
            # checkbox 勾选/取消 → 更新 self._selected
            data = item.data(Qt.ItemDataRole.UserRole)
            if data:
                ip_type, orig_i = data
                pool = self._dc if ip_type == "dc" else self._res
                if 0 <= orig_i < len(pool):
                    ip_key = self._ip_key(pool[orig_i])
                    if item.checkState() == Qt.CheckState.Checked:
                        self._selected[ip_key] = (ip_type, orig_i)
                    else:
                        self._selected.pop(ip_key, None)
            return
        if item.column() != _NOTE_COL:
            return
        ip_key = item.data(Qt.ItemDataRole.UserRole)
        if not ip_key:
            return
        notes = config.get("webshare_notes", {})
        text = item.text().strip()
        if text:
            notes[ip_key] = text
        else:
            notes.pop(ip_key, None)
        config.set_value("webshare_notes", notes)

    # ---------- 列宽 / 行拖拽排序 ----------

    def _on_col_resized(self, col_key: str, table: DragTableWidget, col: int, new: int) -> None:
        if getattr(table, "_suppress_resize", False):
            return
        widths = config.get(col_key, {})
        widths[str(col)] = new
        config.set_value(col_key, widths)

    def _on_row_dropped(self, ip_type: str, src_row: int, dst_row: int) -> None:
        pool = self._dc if ip_type == "dc" else self._res
        if src_row < 0 or dst_row < 0 or src_row >= len(pool) or src_row == dst_row:
            return
        item = pool.pop(src_row)
        insert_at = dst_row if dst_row <= len(pool) else len(pool)
        pool.insert(insert_at, item)
        config.set_value(f"webshare_{ip_type}_order", [self._ip_key(p) for p in pool])
        self._fill(self.dc_table if ip_type == "dc" else self.res_table, pool, ip_type)

    # ---------- 生成 / 取消中转 ----------

    def _collect_checked(self) -> list[tuple[str, int]]:
        return list(self._selected.values())

    def _generate(self) -> None:
        """批量生成（勾选 + 顶部按钮，保留兼容）。"""
        server_id = self.server_combo.currentData()
        if not server_id:
            self._set_status("请先选择前置服务器", warn=True)
            return
        checked = self._collect_checked()
        if not checked:
            self._set_status("请先勾选要生成的 IP（表格第一列勾选框）", warn=True)
            return

        # 弹窗选择入口域名（Cloudflare 主域名 + 二级域名）
        dlg = DomainDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        entry_domain = dlg.entry_domain()
        if not entry_domain:
            self._set_status("未填写二级域名", warn=True)
            return
        self._generate_relays(server_id, entry_domain, checked)

    def _generate_one(self, ip_type: str, idx: int) -> None:
        """单个 IP 生成中转（操作列「生成中转」按钮）。"""
        server_id = self.server_combo.currentData()
        if not server_id:
            self._set_status("请先选择前置服务器", warn=True)
            return
        dlg = DomainDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        entry_domain = dlg.entry_domain()
        if not entry_domain:
            self._set_status("未填写二级域名", warn=True)
            return
        self._generate_relays(server_id, entry_domain, [(ip_type, idx)])

    def _generate_relays(self, server_id: str, entry_domain: str, checked: list) -> None:
        """核心：为 checked 的 IP 生成 relay 并部署。"""
        # 生成中转前刷新订阅信息（轻量，只拉 plans+流量统计，供后续分配用户弹窗用）
        self._refresh_subscription()
        relays = config.get("relays", [])
        used = {int(x.get("relay_port", 0)) for x in relays}
        # 端口单调递增：新 relay 端口必须高于历史最高端口，删除后不复用，防旧链接端口串到新 relay
        high_water = config.get("relay_port_high_water", 0) or 0
        if high_water < RELAY_PORT_START:
            high_water = max(used) if used else RELAY_PORT_START - 1
        seen = {(r.get("host"), int(r.get("port", 0)), r.get("user")) for r in relays}
        pw_pool = config.get("relay_password_pool", {})
        returned_ips = config.get("returned_ips", {}) or {}
        added = 0
        for ip_type, idx in checked:
            pool = self._dc if ip_type == "dc" else self._res
            p = pool[idx]
            host = p.get("proxy_address") or "p.webshare.io"
            port = int(p.get("port", 0))
            user = p.get("username", "")
            if (host, port, user) in seen:
                continue
            country = p.get("country_code", "")
            city = p.get("city_name", "")
            kind = "住宅" if ip_type == "res" else "数据中心"
            country_cn = _cn_country(country)
            city_cn = _cn_city(city)
            date_str = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")
            if city_cn:
                name = f"{date_str}-{kind}-{country_cn}-{city_cn}"
            else:
                name = f"{date_str}-{kind}-{country_cn}"
            rp = next_relay_port(used, start=high_water + 1)
            key = f"{host}:{port}"
            # 同样 IP 复用之前取消时保留的完整凭据（SS + Reality + Hysteria2，客户端不用改）
            cached = pw_pool.get(key) or {}
            if isinstance(cached, str):
                cached = {"relay_password": cached}  # 旧格式：单个密码字符串，仅 SS 密码复用
            elif not isinstance(cached, dict):
                cached = {}
            relay_pw = cached.get("relay_password") or gen_relay_password()
            reality = generate_reality()
            if cached.get("reality_uuid") and cached.get("reality_private_key"):
                reality = {
                    "reality_uuid": cached["reality_uuid"],
                    "reality_private_key": cached["reality_private_key"],
                    "reality_public_key": cached["reality_public_key"],
                    "reality_short_id": cached["reality_short_id"],
                }
            hyst_pw = cached.get("hysteria_password") or gen_relay_password()
            relays.append({
                "id": gen_relay_id(), "server_id": server_id, "name": name,
                "host": host, "port": port, "user": user,
                "password": p.get("password", ""),
                "relay_port": rp, "relay_password": relay_pw,
                "ip_type": ip_type,
                "entry_domain": entry_domain,
                "reality_uuid": reality["reality_uuid"],
                "reality_private_key": reality["reality_private_key"],
                "reality_public_key": reality["reality_public_key"],
                "reality_short_id": reality["reality_short_id"],
                "reality_sni": "www.cloudflare.com",
                "hysteria_password": hyst_pw,
            })
            used.add(rp)
            seen.add((host, port, user))
            returned_ips.pop(key, None)  # 重新生成中转 → 清除「回归」标记
            added += 1
            if rp > high_water:
                high_water = rp
        config.set_value("relays", relays)
        config.set_value("relay_port_high_water", high_water)
        config.set_value("returned_ips", returned_ips)
        # 更新 relay 匹配表 + 重新渲染
        self._relay_map = {f"{r.get('host','')}:{r.get('port','')}": r for r in relays}
        self._fill(self.dc_table, self._dc, "dc")
        self._fill(self.res_table, self._res, "res")
        self._update_relay_overview()
        self._load_servers()
        if added:
            self._sync_entry_dns(entry_domain, server_id)
            self._set_status(f"已生成 {added} 个节点，正在部署到服务器…", loading=True)
            self._apply()  # 生成后自动部署，一步到位
        else:
            self._set_status("选中的 IP 都已生成过，无新增", warn=True)

    def _sync_entry_dns(self, entry_domain: str, server_id: str) -> None:
        """入口域名 DNS 指向前置服务器 IP（失败重试 3 次 + 明确告警，不静默失败）。"""
        token = config.get("cf_token", "")
        server_ip = next((ip for sid, ip, _ in self._servers if sid == server_id), "")
        if not token:
            self._set_status("⚠️ 缺少 CF 令牌，域名 DNS 未创建，客户端将无法连接", warn=True)
            return
        if not server_ip:
            self._set_status(f"⚠️ 找不到服务器 IP，域名 {entry_domain} DNS 未创建", warn=True)
            return

        def _do():
            last_err = ""
            for attempt in range(3):
                try:
                    zid = cloudflare.get_zone_id(token, entry_domain)
                    action, _ = cloudflare.sync_domain(token, zid, entry_domain, server_ip, ttl=60)
                    return ("ok", f"{entry_domain} → {server_ip}（{action}）")
                except Exception as e:
                    last_err = str(e)
                    if attempt < 2:
                        time.sleep(2)
            return ("fail", last_err)

        w = CallWorker(_do)
        w.done.connect(lambda r: self._on_dns_synced(entry_domain, r))
        w.failed.connect(lambda m: self._set_status(f"⚠️ DNS 同步异常：{m}", warn=True))
        self._workers.append(w)
        w.finished.connect(w.deleteLater)
        w.start()

    def _on_dns_synced(self, entry_domain: str, r) -> None:
        if r and r[0] == "ok":
            self._set_status(r[1], ok=True)
        else:
            self._set_status(
                f"⚠️ 域名 {entry_domain} DNS 未创建（{r[1] if r else '未知'}）！节点会连不通，请检查 CF 令牌",
                warn=True,
            )

    def _cancel_relay(self, ip_key: str, table: DragTableWidget, ip_type: str) -> None:
        relay = self._relay_map.get(ip_key)
        if not relay:
            self._set_status("未找到该 IP 的中转节点", warn=True)
            return
        rid = relay["id"]
        server_id = relay.get("server_id", "")
        relay_port = int(relay.get("relay_port", 0))
        socks_p = relay_port + 10000
        # 先拿 server_ip（在 _load_servers 重建 self._servers 之前，避免 Vultr API 失败导致查找不到）
        server_ip = next((ip for sid, ip, _ in self._servers if sid == server_id), "")
        # 保留完整凭据到 pool（同样 IP 重新生成时全复用，SS/Reality/Hysteria2 客户端都不用改）
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
        relays = [r for r in config.get("relays", []) if r["id"] != rid]
        config.set_value("relays", relays)
        # 从拖拽顺序里剔除该 IP（取消中转后回到页面时按官网顺序插回）
        _order_key = f"webshare_{relay.get('ip_type', 'dc')}_order"
        _order = config.get(_order_key, []) or []
        _ipk = f"{relay.get('host','')}:{relay.get('port','')}"
        if _ipk in _order:
            _order.remove(_ipk)
            config.set_value(_order_key, _order)
        # per-relay sing-box：取消中转只删该 relay 的独立 sing-box 服务（不再重建单进程 config）
        sb_cmd = (f"systemctl disable --now sing-box-relay-{rid} 2>/dev/null; "
                  f"rm -f /etc/systemd/system/sing-box-relay-{rid}.service /etc/sing-box/relay-{rid}.json; ")
        self._relay_map = {f"{r.get('host','')}:{r.get('port','')}": r for r in relays}
        self._fill(self.dc_table, self._dc, "dc")
        self._fill(self.res_table, self._res, "res")
        self._update_relay_overview()
        self._load_servers()
        if not server_ip:
            self._set_status("已从配置取消，但找不到前置服务器 IP，需手动清理服务器", warn=True)
            return
        # 直接 SSH 删除指定 relay 服务（可靠，不依赖 apply_relays 全量对比 + 避免并发竞态）
        self._set_status("正在从服务器取消中转…", loading=True)
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

        w = CallWorker(_do)
        w.done.connect(lambda t: self._on_cancelled(t))
        w.failed.connect(lambda m: self._on_cancelled(f"失败：{m}"))
        self._workers.append(w)
        w.finished.connect(w.deleteLater)
        w.start()

    def _on_cancelled(self, text: str) -> None:
        lines = str(text).strip().splitlines()
        self._set_status(f"已取消中转：{lines[-1] if lines else text}", ok="DONE" in str(text))

    def _apply(self) -> None:
        relays = config.get("relays", [])
        if not relays:
            self._set_status("没有可部署的中转节点", warn=True)
            return
        # 部署所有 relay 到各自挂的服务器（不再依赖下拉框选中，避免选错服务器导致部署 0 个）
        targets: list[tuple[str, str]] = []
        seen = set()
        for r in relays:
            sid = r.get("server_id", "")
            if not sid or sid in seen:
                continue
            seen.add(sid)
            srv_ip = next((ip for s, ip, _ in self._servers if s == sid), "")
            if not srv_ip:
                self._set_status(f"找不到服务器 {sid[:8]} 的 IP，点「刷新服务器」", warn=True)
                return
            targets.append((sid, srv_ip))
        self._set_status("正在部署到服务器…", loading=True)
        self.apply_btn.setEnabled(False)

        root = Path(__file__).resolve().parent.parent.parent
        script = root / "scripts" / "apply_relays.py"

        def _do():
            outs = []
            for sid, srv_ip in targets:
                r = subprocess.run(
                    ["uv", "run", "python", str(script), sid, srv_ip],
                    capture_output=True, text=True, timeout=900, cwd=str(root),
                )
                outs.append(r.stdout + r.stderr)
            return "\n".join(outs)

        w = CallWorker(_do)
        w.done.connect(self._on_applied)
        w.failed.connect(lambda m: self._on_applied(f"失败：{m}"))
        self._workers.append(w)
        w.finished.connect(w.deleteLater)
        w.start()

    def _on_applied(self, text: str) -> None:
        self.apply_btn.setEnabled(True)
        lines = str(text).strip().splitlines()
        self._set_status(f"应用完成：{lines[-1] if lines else text}", ok="DONE" in str(text))

    # ---------- 状态 ----------

    def _set_status(self, msg: str, ok: bool = False, warn: bool = False, loading: bool = False) -> None:
        color = "#0a84ff" if loading else ("#32d74b" if ok else ("#ff9f0a" if warn else "#f2f2f7"))
        self.status.setText(f'<span style="color:{color};">{msg}</span>')
