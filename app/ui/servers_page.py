"""服务器页：服务器池管理 + 本地转发代理控制（核心页）。"""

from __future__ import annotations

import subprocess
import time
import uuid
from pathlib import Path

from PySide6.QtCore import QEvent, QItemSelectionModel, QTimer, Qt
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .. import api_client, check, config, domain, e2e, gencreds
from ..api_client import ApiError
from ..cloudflare import get_zone_id, list_dns_records, sync_domain
from ..localproxy import Upstream
from ..relay import gen_relay_id, gen_relay_password, next_relay_port
from ..share import hysteria2_link, socks5_link, ss_link, vless_reality_link
from ..vultr import (
    COUNTRY_NAMES,
    COUNTRY_REGIONS,
    PLAN_MONTHLY_PRICE,
    PLAN_NAMES,
    REGION_CITY,
    REGION_SPEED,
    VultrAPI,
    VultrError,
    build_gost_user_data,
    compute_billing,
    compute_bandwidth,
    compute_account_bandwidth,
    country_of,
    region_label,
)
from .proxy_controller import ProxyStatusCard
from .drag_table import DragTableWidget
from .workers import CallWorker, ProgressWorker

COLS = ["序号", "名称", "区域", "IP", "域名", "状态", "流量(已用/剩余)", "到期时间", "备注", "操作"]


def _verify_dns(sub: str, new_ip: str, progress) -> None:
    """切换后验证 Cloudflare DNS 已指向新 IP（供 _switch_ip 调用）。"""
    if not sub:
        progress("   ⚠️ 无域名，跳过 DNS 验证")
        return
    try:
        cf_token = config.get("cf_token", "")
        zone_id = config.get("zone_id", "") or get_zone_id(cf_token, sub)
        recs = list_dns_records(cf_token, zone_id)
        hit = next((r for r in recs if r["name"] == sub), None)
        if hit and hit["content"] == new_ip:
            progress(f"   ✅ DNS 已生效：{sub} → {new_ip}（客户端 TTL 30s 内自动刷新）")
        elif hit:
            progress(f"   ⚠️ DNS 记录当前 {hit['content']}，Cloudflare 同步中，客户端稍后自动跟随")
        else:
            progress(f"   ⚠️ 未找到 {sub} 的 DNS 记录，需检查")
    except Exception as e:
        progress(f"   ⚠️ DNS 验证跳过（{e}）")


def _sync_dns_direct(sub: str, new_ip: str, progress) -> None:
    """切换后直连 Cloudflare 把子域名 A 记录更新到新 IP（不依赖 Worker/控制机转发）。"""
    if not sub or not new_ip:
        progress("   ⚠️ 无域名或 IP，跳过 DNS 同步")
        return
    try:
        token = config.get("cf_token", "")
        if not token:
            progress("   ⚠️ 无 CF token，跳过 DNS 同步")
            return
        zid = config.get("zone_id", "") or get_zone_id(token, sub)
        sync_domain(token, zid, sub, new_ip)
        progress(f"   ✅ DNS 已同步：{sub} → {new_ip}")
    except Exception as e:
        progress(f"   ⚠️ DNS 同步失败（{e}）")


class ServersPage(QWidget):
    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self._controller = controller
        self._workers: list[CallWorker] = []
        self._servers: list[dict] = []
        self._bandwidth: dict[str, dict] = {}
        self._account_bw: dict = {}
        self._suppress_note = False
        self._suppress_resize = False
        self._controller.log_changed.connect(self._log)
        self._build()
        self._controller.set_builder(self._build_for_proto)
        self._controller.set_protocols(["SOCKS5", "SS", "Reality", "Hysteria2"])
        self._restore_upstream()
        self.refresh()
        self._start_auto_refresh()

    # ---------------- UI ----------------
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(16)

        # 本地代理状态卡（共享逻辑，每页独立 UI）
        root.addWidget(ProxyStatusCard(self._controller))

        # 计费
        self._billing_label = QLabel("")
        root.addWidget(self._billing_label)
        # 账户级流量（Vultr 官方口径：免费 2TB + 实例按小时累计，账户共享）
        self._bw_label = QLabel("")
        root.addWidget(self._bw_label)

        # 表格
        self.table = DragTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(52)
        header = self.table.horizontalHeader()
        header.setStretchLastSection(True)
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        for c in range(len(COLS) - 1):
            header.setSectionResizeMode(c, QHeaderView.ResizeMode.Interactive)
        self.table.setColumnWidth(0, 50)
        self.table.setColumnWidth(1, 150)
        self.table.setColumnWidth(2, 110)
        self.table.setColumnWidth(3, 120)
        self.table.setColumnWidth(4, 140)
        self.table.setColumnWidth(5, 70)
        self.table.setColumnWidth(6, 110)
        self.table.setColumnWidth(7, 150)
        # 恢复上次保存的列宽（用户拖动调整后持久化）
        self._suppress_resize = True
        saved = config.get("table_column_widths", {})
        for c, w in saved.items():
            try:
                # 跳过 stretch 列残留的异常宽度（旧版操作列拉伸后可达 700+px）
                if int(w) < 400:
                    self.table.setColumnWidth(int(c), int(w))
            except (ValueError, IndexError):
                pass
        self._suppress_resize = False
        header.sectionResized.connect(self._on_col_resized)
        QApplication.instance().installEventFilter(self)
        self.table.itemChanged.connect(self._on_note_changed)
        self.table.cellDoubleClicked.connect(self._on_cell_dblclick)
        self.table.row_dropped.connect(self._on_row_dropped)
        root.addWidget(self.table, 1)

        # 切换IP 日志回显
        self._log_view = QTextEdit()
        self._log_view.setReadOnly(True)
        self._log_view.setFixedHeight(150)
        root.addWidget(self._log_view)

        # 底部按钮 + 反馈
        btns = QHBoxLayout()
        self._create_btn = QPushButton("新建服务器")
        self._create_btn.setProperty("primary", True)
        self._create_btn.clicked.connect(self._open_create)
        btns.addWidget(self._create_btn)
        self._refresh_btn = QPushButton("刷新")
        self._refresh_btn.clicked.connect(self.refresh)
        btns.addWidget(self._refresh_btn)
        self._detect_btn = QPushButton("检测连通性")
        self._detect_btn.clicked.connect(self._detect_blocked)
        self._detect_btn.setEnabled(False)  # 没设上游时禁用
        btns.addWidget(self._detect_btn)
        self._clear_btn = QPushButton("清空")
        self._clear_btn.clicked.connect(self._clear_log)
        btns.addWidget(self._clear_btn)
        btns.addStretch(1)
        self._op_label = QLabel("")
        btns.addWidget(self._op_label)
        root.addLayout(btns)

    # ---------------- 上游构造（builder） ----------------
    def _make_upstream(self, srv: dict, proto: str) -> Upstream | None:
        """根据协议构造上游。Reality/Hysteria2 会启动本地客户端，失败返回 None。"""
        if proto in ("Reality", "Hysteria2"):
            creds = gencreds.get_credentials(srv["id"])
            local_port = 1081 if proto == "Reality" else 1082
            server_host = srv["ip"]
            if proto == "Hysteria2":
                dom = domain.server_domain(srv["id"])
                if dom:
                    server_host = dom
            if self._controller.proto_client.start(proto, creds, server_host, local_port):
                return Upstream(host="127.0.0.1", port=local_port, name=srv["name"], protocol="socks5")
            return None
        self._controller.proto_client.stop()
        if proto == "SS":
            creds = gencreds.get_credentials(srv["id"])
            return Upstream(
                host=srv["ip"],
                port=int(creds.get("ss_port", 45678)),
                password=creds.get("ss_password", ""),
                name=srv["name"],
                protocol="ss",
            )
        return Upstream(
            host=srv["ip"],
            port=srv["port"],
            user=srv["user"],
            password=srv["password"],
            name=srv["name"],
            protocol="socks5",
        )

    def _build_for_proto(self, proto: str) -> Upstream | None:
        """builder：根据协议 + 当前活跃服务器（cache）构造上游。"""
        cache = config.get("active_upstream_cache") or {}
        if not cache.get("host"):
            return None
        srv = {
            "id": cache.get("id", ""),
            "ip": cache.get("host", ""),
            "port": int(cache.get("port", 1080)),
            "user": cache.get("user", ""),
            "password": cache.get("password", ""),
            "name": cache.get("name", ""),
        }
        return self._make_upstream(srv, proto)

    def _upstream_text(self, srv: dict, up: Upstream) -> str:
        sid = srv["id"]
        sub = domain.server_domain(sid)
        note = config.get("server_notes", {}).get(sid, "")
        extra = ""
        if sub:
            extra += f' <span style="color:#0a84ff;">{sub}</span>'
        if note:
            extra += f' <span style="color:#98989d;">[{note}]</span>'
        return (
            f'当前上游：<span style="color:#f2f2f7;">{up.name}</span> '
            f'<span style="color:#98989d;">({up.host}:{up.port})</span>{extra}'
        )

    def _restore_upstream(self) -> None:
        cache = config.get("active_upstream_cache")
        if not cache:
            return
        srv = {
            "id": cache.get("id", ""),
            "ip": cache.get("host", ""),
            "port": int(cache.get("port", 1080)),
            "user": cache.get("user", ""),
            "password": cache.get("password", ""),
            "name": cache.get("name", ""),
        }
        up = self._make_upstream(srv, self._controller.current_proto())
        if up is None:
            return
        self._controller.set_active(up, cache, self._upstream_text(srv, up))
        self._update_detect_btn()

    def activate(self) -> None:
        """页面激活时：接入共享 controller 的 builder + 协议范围。"""
        self._controller.set_builder(self._build_for_proto)
        self._controller.set_protocols(["SOCKS5", "SS", "Reality", "Hysteria2"])

    def shutdown(self) -> None:
        """App 退出前等在途 worker 结束（代理停由共享 controller 负责）。"""
        for w in self._workers:
            if w.isRunning():
                w.wait(2000)

    def _update_billing(self, billing: dict | None) -> None:
        """更新计费显示：预付余额、本月已用、剩余、每小时扣费、剩余可用天数。"""
        self._billing_cache = billing
        if not billing:
            self._billing_label.setText("")
            return
        credit = billing["credit"]
        owed = billing["owed"]
        pending = billing["pending"]
        remaining = billing["remaining"]
        hourly = billing["total_hourly"]
        count = billing["running_count"]
        days = billing["days_left"]
        parts = []
        if credit > 0:
            parts.append(f'💰 预付余额 <b>${credit:.2f}</b>')
        elif owed > 0:
            parts.append(f'<span style="color:#ff453a;">欠费 ${owed:.2f}</span>')
        if pending > 0:
            parts.append(f'本月已用 ${pending:.2f}')
        if credit > 0:
            if remaining > 0:
                parts.append(f'剩余 <span style="color:#32d74b;">${remaining:.2f}</span>')
            else:
                parts.append('<span style="color:#ff453a;">余额已用完</span>')
        parts.append(f'{count} 台运行')
        if hourly > 0:
            parts.append(f'每小时扣 ${hourly:.4f}')
        if days is not None:
            parts.append(f'<span style="color:#98989d;">同时运行可撑 {days:.1f} 天</span>')
        # 动态天数：考虑各台服务器按到期时间逐个销毁后的余额耗尽时间
        dyn_days = self._dynamic_days_left(remaining)
        if dyn_days is not None:
            parts.append(f'<span style="color:#32d74b;">按到期时间可撑 {dyn_days:.1f} 天</span>')
        else:
            parts.append('<span style="color:#32d74b;">按到期时间：余额充足</span>')
        # 剩余可分配：用「剩余余额（remaining）」而非预付余额（credit），因为本月已用（pending）已消耗
        alloc = self._allocatable_text(remaining)
        if alloc:
            parts.append(alloc)
        self._billing_label.setText('　'.join(parts))

    def _monthly_price(self, srv: dict) -> float:
        """返回服务器月价（美元/月），按套餐 id 或显示名查 PLAN_MONTHLY_PRICE。"""
        plan_key = srv.get("plan", "")
        monthly = PLAN_MONTHLY_PRICE.get(plan_key, 0.0)
        if monthly <= 0:
            for k, name in PLAN_NAMES.items():
                if name == plan_key:
                    monthly = PLAN_MONTHLY_PRICE.get(k, 0.0)
                    break
        return monthly

    def _expiry_days(self, srv: dict) -> int | None:
        """返回服务器到期剩余天数（None = 未设到期时间，跟随余额动态 = 视为永久）。"""
        from datetime import datetime
        exp_str = (config.get("server_expiry", {}).get(srv.get("id", ""), "") or "")[:10]
        if not exp_str:
            return None
        try:
            exp_date = datetime.strptime(exp_str, "%Y-%m-%d").date()
        except ValueError:
            return None
        return max(0, (exp_date - datetime.now().date()).days)

    def _dynamic_days_left(self, credit: float) -> float | None:
        """动态余额天数：考虑各台服务器按到期时间逐个销毁后的余额耗尽时间。
        未设到期时间的服务器视为永久（一直扣费）。"""
        events = []          # (到期剩余天数, 每小时扣费)
        permanent_hourly = 0.0
        for srv in getattr(self, "_servers", []):
            if srv.get("type") == "manual":
                continue
            monthly = self._monthly_price(srv)
            if monthly <= 0:
                continue
            hourly = monthly / 720.0
            if self._is_control(srv):
                permanent_hourly += hourly  # 控制机实际不销毁，永久扣费
                continue
            days = self._expiry_days(srv)
            if days is None:
                permanent_hourly += hourly  # 未设到期时间 = 永久（一直扣费）
            else:
                events.append((days, hourly))
        if not events and permanent_hourly <= 0:
            return None
        events.sort()
        balance = credit
        current_day = 0
        current_hourly = permanent_hourly + sum(h for _, h in events)
        if current_hourly <= 0:
            return None
        for days, hourly in events:
            if days <= current_day:
                current_hourly -= hourly  # 已到期，停止扣费
                continue
            seg = days - current_day
            seg_cost = current_hourly * 24.0 * seg
            if balance <= seg_cost:
                return current_day + balance / (current_hourly * 24.0)
            balance -= seg_cost
            current_day = days
            current_hourly -= hourly
        if current_hourly > 0:
            return current_day + balance / (current_hourly * 24.0)
        return None  # 所有服务器都到期销毁，余额用不完

    def _allocatable_text(self, credit: float) -> str:
        """计算「剩余可分配天数」：余额扣除现有服务器到各自到期时间的消耗后，
        还能支撑多少天的 2.5/5/6 美元档服务器。"""
        from datetime import datetime
        committed = 0.0
        for srv in getattr(self, "_servers", []):
            if srv.get("type") == "manual":
                continue
            monthly = self._monthly_price(srv)
            if monthly <= 0:
                continue
            days = self._expiry_days(srv)
            if days is None:
                # 跟随余额 = 按账户动态到期算（会一直跑到余额耗尽）
                account_exp = getattr(self, "_account_expiry", "") or ""
                if account_exp:
                    try:
                        days = max(0, (datetime.strptime(account_exp, "%Y-%m-%d").date() - datetime.now().date()).days)
                    except ValueError:
                        continue
                else:
                    continue
            committed += monthly * days / 30.0
        left = credit - committed
        if left <= 0:
            return '<span style="color:#ff453a;">已分配完，无可分配余额</span>'
        d2 = left / (2.5 / 30.0)
        d5 = left / (5.0 / 30.0)
        d6 = left / (6.0 / 30.0)
        return f'可再分配：<span style="color:#ff9f0a;">2.5美元档≈{d2:.0f}天</span> / <span style="color:#ff9f0a;">5美元档≈{d5:.0f}天</span> / <span style="color:#ff9f0a;">6美元档≈{d6:.0f}天</span>'

    def _update_account_bw(self, account_bw: dict) -> None:
        """更新账户级流量显示（Vultr 官方口径：免费 2TB + 实例按小时累计，账户共享，非每实例独立）。"""
        if not account_bw:
            self._bw_label.setText("")
            return
        total = account_bw.get("total_gb", 0)
        used = account_bw.get("used_gb", 0)
        remaining = account_bw.get("remaining_gb", 0)
        overage = account_bw.get("overage_gb", 0)
        free = account_bw.get("free_gb", 0)
        inst = account_bw.get("instance_gb", 0)
        parts = [f'🌐 <b>账户流量</b>（所有服务器共享·含销毁重建累计）：已用 <b>{used:.0f}G</b> / 总 <b>{total:.0f}G</b>']
        if remaining > 0:
            parts.append(f'剩余 <span style="color:#32d74b;">{remaining:.0f}G</span>')
        if overage > 0:
            parts.append(f'<span style="color:#ff453a;">超额 {overage:.0f}G（${account_bw.get("overage_cost", 0):.2f}）</span>')
        self._bw_label.setText('　'.join(parts))

    # ---------------- 服务器列表 ----------------
    # ---------------- 服务器列表 ----------------
    def refresh(self) -> None:
        # 节流：10 秒内不重复刷新（避免频繁点刷新触发大量 Vultr API 调用撞限流）
        now = time.time()
        if now - getattr(self, "_last_refresh", 0) < 10:
            return
        self._last_refresh = now
        self._log("⏳ 正在加载服务器列表…")

        def _do():
            servers: list[dict] = []
            api_error = ""
            billing = None
            bandwidth = {}
            account_bw = {}
            account_expiry = ""
            key = config.get("vultr_api_key", "")
            if key:
                try:
                    api = VultrAPI(key)
                    instances = api.list_instances()
                    for inst in instances:
                        servers.append(self._managed_row(inst))
                    account = api.get_account()
                    plans = api.get_plans()
                    billing = compute_billing(instances, plans, account)
                    bandwidth = compute_bandwidth(instances, plans, api)
                    # 账户级真实带宽（Vultr 官方口径：免费 2TB + 实例按小时累计，账户共享）
                    try:
                        account_bw = compute_account_bandwidth(api.get_account_bandwidth())
                    except Exception:
                        account_bw = {}
                    # 账户动态到期（随余额实时变化）。不写死 server_expiry，作为「跟随余额」的默认值。
                    account_expiry = ""
                    if billing and billing.get("days_left"):
                        from datetime import datetime as _dt, timedelta as _td
                        account_expiry = (_dt.now() + _td(days=billing["days_left"])).strftime("%Y-%m-%d")
                except Exception as e:
                    api_error = str(e)  # API 不可用，仍显示手动服务器
                    account_expiry = ""
            for m in config.get("manual_servers", []):
                servers.append(
                    {
                        "id": f"manual:{m['id']}",
                        "type": "manual",
                        "name": m.get("name", "手动节点"),
                        "region": "手动",
                        "ip": m["host"],
                        "port": int(m["port"]),
                        "user": m.get("user", ""),
                        "password": m.get("password", ""),
                        "power": "manual",
                        "plan": "",
                    }
                )
            # 加载分配关系（判断哪些服务器已分配给用户，供「已分配」按钮 + 悬停显示用户名）
            assigned: dict[str, list[str]] = {}
            try:
                assigns = api_client.list_all_assignments().get("assignments", [])
                for a in assigns:
                    if a.get("resource_type") != "vultr":
                        continue
                    rid = a.get("resource_id", "")
                    uname = a.get("username", "")
                    if rid and uname:
                        assigned.setdefault(rid, []).append(uname)
            except Exception:
                pass
            return servers, api_error, billing, bandwidth, account_bw, account_expiry, assigned

        w = CallWorker(_do)
        w.done.connect(self._on_servers)
        w.failed.connect(self._on_refresh_fail)
        self._workers.append(w)
        w.finished.connect(w.deleteLater)
        w.start()

    def _on_refresh_fail(self, m: str) -> None:
        self._log(f"❌ 服务器列表加载失败：{m}")
        self._op_label.setText(f'<span style="color:#ff453a;">加载失败：{m}</span>')

    def _start_auto_refresh(self) -> None:
        """定时刷新：60 秒检测 IP 变化（客户端切 IP 自动同步），5 分钟更新流量。省流量设计。"""
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(60000)  # 60 秒：只查实例列表，检测 IP 变化
        self._refresh_timer.timeout.connect(self._light_refresh)
        self._refresh_timer.start()
        self._bw_timer = QTimer(self)
        self._bw_timer.setInterval(300000)  # 5 分钟：更新流量列（较耗 API 调用）
        self._bw_timer.timeout.connect(self._bw_refresh)
        self._bw_timer.start()
        self._expiry_timer = QTimer(self)
        self._expiry_timer.setInterval(3600000)  # 1 小时：检查到期（提前提醒 + 到期自动销毁）
        self._expiry_timer.timeout.connect(self._check_expiry)
        self._expiry_timer.start()
        self._expiry_notified: set[str] = set()  # 已提醒过的到期实例，避免每小时重复刷屏

    def _light_refresh(self) -> None:
        """轻量刷新：只拉实例列表 + 计费，检测 IP 变化（客户端切 IP 后自动同步）。"""
        key = config.get("vultr_api_key", "")
        if not key or getattr(self, "_light_refreshing", False):
            return
        self._light_refreshing = True

        def _do():
            api = VultrAPI(key)
            instances = api.list_instances()
            account = api.get_account()
            plans = api.get_plans()
            billing = compute_billing(instances, plans, account)
            return billing, instances

        w = CallWorker(_do)
        w.done.connect(self._on_light_refresh)
        w.failed.connect(lambda m: None)  # 静默失败，不打扰用户
        self._workers.append(w)
        w.finished.connect(w.deleteLater)
        w.finished.connect(lambda: setattr(self, "_light_refreshing", False))
        w.start()

    def _on_light_refresh(self, result) -> None:
        billing, instances = result
        self._update_billing(billing)
        if self._detect_ip_changes(instances):
            self._sync_server_configs(instances)  # 迁移域名/配置到新实例
            self.refresh()

    def _detect_ip_changes(self, instances: list) -> bool:
        """对比 Vultr 最新实例 ID/IP 与本地缓存，有增删或 IP 变化返回 True。"""
        cached = getattr(self, "_servers", [])
        if not cached:
            return False
        # 只统计本页管理的实例（label 前缀匹配），排除控制机等其它实例
        label = config.get("label", "vultr-proxy-node")
        managed = [i for i in instances if (i.get("label", "") or "").startswith(label)]
        current_ids = {i["id"] for i in managed}
        cached_ids = {srv["id"] for srv in cached if (srv.get("label", "") or "").startswith(label)}
        if current_ids != cached_ids:
            return True
        current_ip = {i["id"]: i.get("main_ip", "") for i in managed}
        current_power = {i["id"]: i.get("power_status", "") for i in managed}
        for srv in cached:
            if srv["id"] in current_ip and current_ip[srv["id"]] != srv.get("ip", ""):
                return True
            # 新建服务器 power 从 stopped → running 也刷新（状态列实时）
            if srv["id"] in current_power and current_power[srv["id"]] != srv.get("power", ""):
                return True
        return False

    def _migrate_server_config(self, old_id: str, new_id: str) -> None:
        """把旧实例 ID 的 per-server 配置（域名/凭据/备注/名称/到期/累计流量）迁移到新实例 ID。"""
        for key in ("server_domains", "server_configs", "server_notes", "server_names", "server_expiry", "server_cumulative_used"):
            def _migrate(m):
                m = dict(m or {})
                if old_id in m:
                    m[new_id] = m.pop(old_id)
                return m
            config.mutate(key, _migrate)

    def _sync_server_configs(self, instances: list) -> None:
        """检测实例增删 + 扫描 config 旧 UUID，把消失实例的配置迁移到新增实例（客户端切 IP 后自动同步）。"""
        cached = getattr(self, "_servers", [])
        # 只统计本页管理的实例（label 前缀匹配），排除控制机等其它实例
        label = config.get("label", "vultr-proxy-node")
        managed = [i for i in instances if (i.get("label", "") or "").startswith(label)]
        current_ids = {i["id"] for i in managed}
        cached_ids = {srv["id"] for srv in cached if (srv.get("label", "") or "").startswith(label)}
        gone = sorted(cached_ids - current_ids)
        added = sorted(current_ids - cached_ids)

        # 主动扫描 config 里的旧 UUID（指向已销毁实例），不依赖 cached——
        # 覆盖「服务端停机期间客户端切换 IP」的情况（此时 cached 检测不到 gone）
        domains = dict(config.get("server_domains", {}) or {})
        stale_ids = [uid for uid in domains if uid not in current_ids]

        if not gone and not stale_ids:
            return
        # 从 Worker 读切换记录（old_id → new_id 精确映射，避免 gone/added 排序错配把配置迁到错误实例）
        try:
            flow_data = api_client.list_switch_flow()
            self._switch_old_flow = {s.get("old_id"): float(s.get("old_used_gb", 0) or 0) for s in flow_data.get("switches", [])}
        except Exception:
            self._switch_old_flow = {}
            flow_data = {"switches": []}
        # 建立完整切换链（old → new），支持多步追踪（含 pending 记录，新机已 running 但客户端没轮询完成）
        flow_map = {s.get("old_id"): s.get("new_id") for s in flow_data.get("switches", []) if s.get("old_id") and s.get("new_id")}

        def _trace(uid: str) -> str:
            cur = uid
            seen = set()
            while cur in flow_map and cur not in seen:
                seen.add(cur)
                cur = flow_map[cur]
            return cur

        # 用切换链追踪迁移（gone 的旧机 + config 里指向已销毁实例的旧 UUID → 链上最终新机）
        targets = set(gone) | set(stale_ids)
        migrated = 0
        for old_id in targets:
            final_id = _trace(old_id)
            if final_id == old_id or final_id not in current_ids:
                continue
            old_used = self._switch_old_flow.get(old_id, 0)
            if old_used <= 0:
                old_used = (self._bandwidth.get(old_id, {}) or {}).get("used_gb", 0)
            if old_used > 0:
                def _accum(m):
                    m = dict(m or {})
                    # 累加（不是覆盖）：历史累计 + 这次旧实例出站
                    m[old_id] = float(m.get(old_id, 0) or 0) + old_used
                    return m
                config.mutate("server_cumulative_used", _accum)
            self._migrate_server_config(old_id, final_id)
            self._log(f"🔄 检测到 IP 切换：{old_id[:8]} → {final_id[:8]}，已同步域名/配置/流量")
            migrated += 1
        # 切换记录里匹配不到的，只在 gone/added 各 1 个时保守迁移；否则跳过（宁可不同步也不乱迁）
        if migrated == 0 and len(gone) == 1 and len(added) == 1:
            old_id, new_id = gone[0], added[0]
            self._migrate_server_config(old_id, new_id)
            self._log(f"🔄 检测到实例变化：{old_id[:8]} → {new_id[:8]}，已同步配置")
        elif migrated == 0:
            self._log(f"ℹ️ 检测到实例变化（消失 {len(gone)} 台，新增 {len(added)} 台），无匹配切换记录，跳过迁移以防错配")

    def _bw_refresh(self) -> None:
        """低频刷新流量列（compute_bandwidth 较耗 API 调用，5 分钟一次）。"""
        key = config.get("vultr_api_key", "")
        if not key or getattr(self, "_bw_refreshing", False):
            return
        self._bw_refreshing = True

        def _do():
            api = VultrAPI(key)
            instances = api.list_instances()
            plans = api.get_plans()
            bandwidth = compute_bandwidth(instances, plans, api)
            return bandwidth

        w = CallWorker(_do)
        w.done.connect(self._on_bw_refresh)
        w.failed.connect(lambda m: None)
        self._workers.append(w)
        w.finished.connect(w.deleteLater)
        w.finished.connect(lambda: setattr(self, "_bw_refreshing", False))
        w.start()

    def _on_bw_refresh(self, bandwidth) -> None:
        self._bandwidth = bandwidth
        self._update_bandwidth_column()
        self._sync_flow_to_d1(bandwidth)
        self._purge_stale_assignments()

    def _sync_flow_to_d1(self, bandwidth: dict) -> None:
        """把每台实例的累计已用流量同步到 D1（worker /admin/sync-flow）。

        客户端据此判断「流量是否用完」实现断流。静默失败，不影响 UI。
        """
        updates = [
            {"resource_id": iid, "used_gb": round(float(bw.get("used_gb", 0) or 0), 2)}
            for iid, bw in bandwidth.items()
        ]
        if not updates:
            return
        try:
            api_client.sync_flow(updates)
        except ApiError:
            pass
        except Exception:
            pass

    def _purge_stale_assignments(self) -> None:
        """后台清理指向已销毁实例的分配记录（实例销毁 → 客户端列表消失）。

        保护切换 IP 中间态：首次发现实例消失只记时间戳，超过 10 分钟仍未出现
        （远超切换 IP 的 1-2 分钟）才确认销毁并清理分配，避免误删切换中的节点。
        """
        if getattr(self, "_purging", False):
            return
        self._purging = True

        def _do():
            api = VultrAPI(config.get("vultr_api_key", ""))
            instances = api.list_instances()
            alive_ids = {i["id"] for i in instances}
            assigns = api_client.list_all_assignments().get("assignments", [])
            # 查切换记录：切换中的旧机（old_id）绝不清理——它可能是「切换 IP 卡在中间态」
            # （客户端没轮询完成，assignment 还没回写到新机）。清理它会误删正在工作的节点。
            switch_old_ids = set()
            try:
                flow = api_client.list_switch_flow().get("switches", [])
                switch_old_ids = {s.get("old_id") for s in flow if s.get("old_id")}
            except Exception:
                pass
            since = dict(config.get("stale_assign_since", {}) or {})
            now = time.time()
            to_purge = []  # (username, user_id, rid)，网络调用放锁外，避免长时间持锁

            def _calc(since_dict):
                """锁内原子读-改-写 stale_assign_since，返回新值。"""
                since = dict(since_dict or {})
                for a in assigns:
                    if a.get("resource_type") != "vultr":
                        continue
                    rid = a.get("resource_id", "")
                    if not rid or rid in alive_ids:
                        continue
                    # 实例已不在存活列表，但是某次切换的旧机 → 跳过（切换中间态，非真销毁）
                    if rid in switch_old_ids:
                        since.pop(rid, None)
                        continue
                    if rid not in since:
                        since[rid] = now
                        continue
                    if now - since[rid] < 600:  # 10 分钟内不清理（切换 IP 中间态）
                        continue
                    # 超过 10 分钟仍未出现，确认已销毁，待清理（网络调用放锁外）
                    to_purge.append((a.get("username", ""), a["user_id"], rid))
                    since.pop(rid, None)
                # 清理已恢复实例的时间戳记录
                for rid in list(since.keys()):
                    if rid in alive_ids:
                        since.pop(rid, None)
                return since

            config.mutate("stale_assign_since", _calc)

            # 网络调用（锁外）：真正清理分配记录
            purged = []
            for username, user_id, rid in to_purge:
                try:
                    api_client.unassign(user_id, "vultr", rid)
                    purged.append((username, rid[:8]))
                except ApiError:
                    pass
            return purged

        def _done(purged):
            self._purging = False
            if purged:
                for username, rid in purged:
                    self._log(f"🗑️ 已清理失效分配：{username} 的节点 {rid}（实例已销毁）")

        w = CallWorker(_do)
        w.done.connect(_done)
        w.failed.connect(lambda m: setattr(self, "_purging", False))
        self._workers.append(w)
        w.finished.connect(w.deleteLater)
        w.start()

    def _update_bandwidth_column(self) -> None:
        """只更新「流量」列（索引 6），不重建表格、不影响用户操作。"""
        for r in range(min(self.table.rowCount(), len(self._servers))):
            srv = self._servers[r]
            bw = self._bandwidth.get(srv["id"])
            item = self.table.item(r, 6)
            if item is None:
                item = QTableWidgetItem()
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.table.setItem(r, 6, item)
            item.setText(self._bandwidth_text(bw))
            item.setToolTip(self._bandwidth_tip(bw))

    def _display_name(self, inst: dict) -> str:
        """名称：自定义名 > Vultr-域名前缀 > Vultr label。"""
        custom = config.get("server_names", {}).get(inst["id"], "")
        if custom:
            return custom
        dom = domain.server_domain(inst["id"])
        if dom:
            return "Vultr-" + dom.split(".")[0]
        return "Vultr-" + (inst.get("label", "") or inst["id"])

    def _managed_row(self, inst: dict) -> dict:
        creds = gencreds.get_credentials(inst["id"])
        return {
            "id": inst["id"],
            "type": "managed",
            "name": self._display_name(inst),
            "label": inst.get("label", ""),
            "region": inst.get("region", ""),
            "ip": inst.get("main_ip", "0.0.0.0"),
            "port": int(creds.get("proxy_port", 1080)),
            "user": creds.get("proxy_user", ""),
            "password": creds.get("proxy_pass", ""),
            "power": inst.get("power_status", ""),
            "date_created": inst.get("date_created", ""),
            "plan": PLAN_NAMES.get(inst.get("plan", ""), inst.get("plan", "")),
            "plan_id": inst.get("plan", ""),
        }

    def _on_servers(self, result) -> None:
        servers, api_error, billing, bandwidth, account_bw, account_expiry, assigned = result
        self._servers = servers
        self._bandwidth = bandwidth
        self._account_bw = account_bw
        self._account_expiry = account_expiry
        self._assigned = assigned
        self._populate_table()
        self._update_billing(billing)
        self._update_account_bw(account_bw)
        if api_error:
            self._log(f"⚠️ 服务器加载警告：{api_error}（手动服务器仍显示）")
            self._op_label.setText(
                f'<span style="color:#ff9f0a;">⚠️ 服务器加载警告：{api_error}（点「刷新」重试）</span>'
            )
        else:
            self._log(f"✅ 服务器列表已加载（{len(servers)} 台）")

    def _populate_table(self) -> None:
        self._suppress_note = True
        self.table.setRowCount(0)
        active_id = config.get("active_upstream_id")
        notes = config.get("server_notes", {})
        # 按保存的拖拽顺序排序服务器（未出现在 order 里的放末尾，保持相对稳定）
        order = config.get("server_order", []) or []
        if order:
            order_map = {sid: i for i, sid in enumerate(order)}
            self._servers.sort(key=lambda s: order_map.get(s["id"], len(order) + 9999))
        for srv in self._servers:
            r = self.table.rowCount()
            self.table.insertRow(r)
            idx_item = QTableWidgetItem(str(r + 1))
            idx_item.setData(Qt.ItemDataRole.UserRole, srv["id"])
            idx_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(r, 0, idx_item)
            name_item = QTableWidgetItem(srv["name"])
            if srv["id"] == active_id:
                name_item.setForeground(Qt.GlobalColor.green)
                name_item.setText("✔ " + srv["name"])
            name_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(r, 1, name_item)
            self._set_text(r, 2, region_label(srv["region"]))
            self._set_text(r, 3, srv["ip"])
            dom_item = QTableWidgetItem(domain.server_domain(srv["id"]) or "-")
            dom_item.setToolTip("双击设置子域名")
            dom_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(r, 4, dom_item)
            power = self._power_text(srv["power"])
            status_item = QTableWidgetItem(power)
            status_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(r, 5, status_item)
            bw = self._bandwidth.get(srv["id"])
            bw_item = QTableWidgetItem(self._bandwidth_text(bw))
            bw_item.setToolTip(self._bandwidth_tip(bw))
            bw_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(r, 6, bw_item)
            expiry = config.get("server_expiry", {}).get(srv["id"], "")
            if expiry:
                exp_item = QTableWidgetItem(expiry)
                exp_item.setToolTip("已设固定到期时间；双击修改（留空 = 跟随账户余额动态）")
            else:
                account_exp = getattr(self, "_account_expiry", "") or ""
                exp_item = QTableWidgetItem(account_exp or "-")
                exp_item.setToolTip(f"跟随账户余额动态（当前 ≈ {account_exp}）；双击可设固定到期时间")
            exp_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(r, 7, exp_item)
            note_item = QTableWidgetItem(notes.get(srv["id"], ""))
            note_item.setToolTip("双击编辑备注，便于区分服务器")
            note_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(r, 8, note_item)
            self.table.setCellWidget(r, 9, self._op_cell(srv))
        self._suppress_note = False

    def _on_row_dropped(self, src_row: int, dst_row: int) -> None:
        """拖拽排序：把 self._servers 里 src_row 的元素移到 dst_row 位置，持久化 + 重新渲染。

        直接操作数据列表（pop + insert），再重新 populate——绝不复制、绝不错位。
        """
        if src_row < 0 or dst_row < 0 or src_row >= len(self._servers) or src_row == dst_row:
            return
        srv = self._servers.pop(src_row)
        insert_at = dst_row if dst_row <= len(self._servers) else len(self._servers)
        self._servers.insert(insert_at, srv)
        config.set_value("server_order", [s["id"] for s in self._servers])
        self._populate_table()

    def _on_note_changed(self, item: QTableWidgetItem) -> None:
        if self._suppress_note:
            return
        col = item.column()
        if col not in (1, 8):
            return
        row = item.row()
        if row < 0 or row >= len(self._servers):
            return
        sid = self._servers[row]["id"]
        text = item.text().strip()
        if col == 1:
            if text.startswith("✔ "):
                text = text[2:].strip()
            names = dict(config.get("server_names", {}))
            if text:
                names[sid] = text
            else:
                names.pop(sid, None)
            config.set_value("server_names", names)
            self._populate_table()
            return
        notes = dict(config.get("server_notes", {}))
        if text:
            notes[sid] = text
        else:
            notes.pop(sid, None)
        config.set_value("server_notes", notes)

    def eventFilter(self, obj, event) -> bool:
        """点击表格：序号列选中整行，其它列或空白清除选中。"""
        if obj is self.table.viewport() and event.type() == QEvent.Type.MouseButtonPress:
            index = self.table.indexAt(event.position().toPoint())
            if not index.isValid() or index.column() != 0:
                self.table.clearSelection()
        return super().eventFilter(obj, event)

    def _on_cell_dblclick(self, row: int, col: int) -> None:
        if row < 0 or row >= len(self._servers):
            return
        srv = self._servers[row]
        if col == 4:
            self._edit_subdomain(srv)
            return
        if col == 7:
            self._edit_expiry(srv)
            return
        if col in (1, 8):
            return

    def _edit_expiry(self, srv: dict) -> None:
        """双击「到期时间」列：手动设置该服务器的固定到期时间（留空 = 跟随账户余额动态）。"""
        from datetime import datetime as _dt
        current = config.get("server_expiry", {}).get(srv["id"], "")
        new_exp, ok = QInputDialog.getText(
            self,
            "设置到期时间",
            f'服务器「{srv["name"]}」的固定到期时间（YYYY-MM-DD）：\n\n留空 = 跟随账户余额动态到期（自动延长/缩短）',
            text=current,
        )
        if not ok:
            return
        new_exp = new_exp.strip()
        if new_exp:
            # 校验格式
            try:
                _dt.strptime(new_exp, "%Y-%m-%d")
            except ValueError:
                QMessageBox.warning(self, "提示", "日期格式不对，请用 YYYY-MM-DD（如 2026-12-31）")
                return
        _se = dict(config.get("server_expiry", {}))
        if new_exp:
            _se[srv["id"]] = new_exp
        else:
            _se.pop(srv["id"], None)  # 清空 = 恢复跟随账户余额动态
        config.set_value("server_expiry", _se)
        self._log(f"✅ 已设置「{srv['name']}」到期时间：{new_exp or '跟随账户余额动态'}")
        self._populate_table()
        # 重新算「可再分配」预算（到期时间变了，可分配天数跟着变）
        self._update_billing(getattr(self, "_billing_cache", None))

    def _edit_subdomain(self, srv: dict) -> None:
        current = domain.server_domain(srv["id"]) or ""
        new_dom, ok = QInputDialog.getText(
            self, "设置子域名", f'服务器「{srv["name"]}」的子域名（如 node1.yduas.cc）：', text=current
        )
        if not ok:
            return
        new_dom = new_dom.strip()
        if not new_dom:
            return
        # 域名没变也重新签证书（不 return），域名变了先更新
        if new_dom != current:
            domain.set_server_domain(srv["id"], new_dom)
        ip = srv["ip"]
        self._populate_table()
        self._op_label.setText(
            f'<span style="color:#0a84ff;">正在为「{srv["name"]}」同步 {new_dom}（DNS + 证书）…</span>'
        )

        def _do():
            domain.sync_server_dns(srv["id"], ip)
            for _ in range(18):
                if ip in domain.resolve_domain(new_dom):
                    break
                time.sleep(10)
            # 等 SSH 就绪 + 签证书（重试最多 3 分钟）
            pw = gencreds.get_credentials(srv["id"]).get("root_password", "")
            for _ in range(18):
                try:
                    domain.sign_cert(ip, new_dom, password=pw)
                    return new_dom
                except Exception:
                    time.sleep(10)
            raise RuntimeError("签证书超时：SSH 未就绪")

        w = CallWorker(_do)
        w.done.connect(
            lambda d: (
                self._op_label.setText(f'<span style="color:#32d74b;">✅ 子域名 {d} 已生效（DNS + 证书）</span>'),
                self.refresh(),
            )
        )
        w.failed.connect(lambda m: self._op_label.setText(f'<span style="color:#ff453a;">子域名设置失败：{m}</span>'))
        self._workers.append(w)
        w.finished.connect(w.deleteLater)
        w.start()

    def _on_col_resized(self, col: int, old: int, new: int) -> None:
        if self._suppress_resize:
            return
        widths = config.get("table_column_widths", {})
        widths[str(col)] = new
        config.set_value("table_column_widths", widths)

    def _power_text(self, power: str) -> str:
        if power == "running":
            return "运行中"
        if power == "stopped":
            return "已关机"
        if power == "manual":
            return "手动"
        return power or "未知"

    def _bandwidth_text(self, bw: dict | None) -> str:
        """流量列：该服务器已用 / 剩余（累计口径，切换 IP 后已用继承）。"""
        if not bw:
            return "-"
        used = bw["used_gb"]
        remaining = bw["remaining_gb"]
        if used < 1:
            used_text = f"{used * 1000:.0f}M"
        else:
            used_text = f"{used:.1f}G"
        return f"{used_text} / 剩 {remaining:.0f}G"

    def _bandwidth_tip(self, bw: dict | None) -> str:
        """流量 tooltip：该服务器已用/剩余详情（切换 IP 后已用继承）。"""
        if not bw:
            return "流量未获取（点「刷新」重试）"
        used = bw["used_gb"]
        quota = bw["quota_gb"]
        remaining = bw["remaining_gb"]
        return f"已用 {used:.2f} GB / 套餐 {quota:.0f} GB（剩 {remaining:.1f} GB）；切换 IP 后已用继承；入站免费"

    def _set_text(self, r: int, c: int, text: str) -> None:
        item = QTableWidgetItem(text)
        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self.table.setItem(r, c, item)

    def _is_control(self, srv: dict) -> bool:
        """控制机（如 IPv6 转发机 yduas-IPV6）：label 不是 proxy-node 前缀。禁止切换IP/删除等破坏性操作。"""
        label = config.get("label", "vultr-proxy-node")
        return not (srv.get("label", "") or "").startswith(label)

    def _assign_btn(self, srv: dict) -> QPushButton:
        """分配用户按钮：已分配则显示「已分配」+ 禁用 + 悬停用户名；否则「分配用户」可点击。"""
        rid = srv["id"]
        users = (getattr(self, "_assigned", {}) or {}).get(rid, [])
        if users:
            btn = QPushButton("已分配")
            btn.setProperty("compact", True)
            btn.setFixedHeight(30)
            btn.setEnabled(False)
            btn.setToolTip("已分配给：" + "、".join(users) + "（在用户管理里取消分配后恢复）")
            btn.setStyleSheet("color:#98989d;")
            return btn
        btn = QPushButton("分配用户")
        btn.setProperty("compact", True)
        btn.setFixedHeight(30)
        btn.clicked.connect(lambda _=False, s=srv: self._assign_user(s))
        return btn

    def _op_cell(self, srv: dict) -> QWidget:
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(4, 2, 4, 2)
        lay.setSpacing(6)
        lay.addStretch(1)

        # 控制机（IPv6 转发机）：只允许「分配用户」（改到期时间），禁止切换IP/删除等破坏性操作
        if self._is_control(srv):
            ctl_lbl = QLabel("控制机")
            ctl_lbl.setStyleSheet("color:#98989d;font-size:12px;")
            lay.addWidget(ctl_lbl)
            lay.addWidget(self._assign_btn(srv))
            return w

        active_id = config.get("active_upstream_id", "")
        if srv["id"] == active_id:
            set_active_btn = QPushButton("取消上游")
            set_active_btn.setProperty("compact", True)
            set_active_btn.setFixedHeight(30)
            set_active_btn.setStyleSheet("color: #ff9f0a;")
            set_active_btn.clicked.connect(lambda _: self._clear_upstream())
            lay.addWidget(set_active_btn)
        else:
            set_active_btn = QPushButton("设为上游")
            set_active_btn.setProperty("compact", True)
            set_active_btn.setFixedHeight(30)
            set_active_btn.clicked.connect(lambda _=False, s=srv: self._switch(s))
            lay.addWidget(set_active_btn)

        settings_btn = QPushButton("设置")
        settings_btn.setProperty("compact", True)
        settings_btn.setFixedHeight(30)
        settings_btn.clicked.connect(lambda _=False, s=srv: self._open_server_settings(s))
        lay.addWidget(settings_btn)

        switch = QPushButton("切换IP")
        switch.setProperty("compact", True)
        switch.setFixedHeight(30)
        switch.clicked.connect(lambda _=False, s=srv: self._switch_ip(s))
        lay.addWidget(switch)

        share_btn = QPushButton("分享")
        share_btn.setProperty("compact", True)
        share_btn.setFixedHeight(30)
        share_btn.clicked.connect(lambda _=False, s=srv: self._open_share(s))
        lay.addWidget(share_btn)

        assign_btn = self._assign_btn(srv)
        lay.addWidget(assign_btn)

        restart_btn = QPushButton("重启")
        restart_btn.setProperty("compact", True)
        restart_btn.setFixedHeight(30)
        restart_btn.clicked.connect(lambda _=False, s=srv: self._restart_server(s))
        lay.addWidget(restart_btn)

        if srv["power"] == "running":
            power_btn = QPushButton("关机")
            power_btn.clicked.connect(lambda _=False, s=srv: self._power(s, "halt"))
        else:
            power_btn = QPushButton("开机")
            power_btn.clicked.connect(lambda _=False, s=srv: self._power(s, "start"))
        power_btn.setProperty("compact", True)
        power_btn.setFixedHeight(30)
        lay.addWidget(power_btn)

        delete_btn = QPushButton("删除")
        delete_btn.setProperty("compact", True)
        delete_btn.setProperty("danger", True)
        delete_btn.setFixedHeight(30)
        delete_btn.clicked.connect(lambda _=False, s=srv: self._delete(s))
        lay.addWidget(delete_btn)

        return w

    # ---------------- 操作 ----------------
    def _switch(self, srv: dict) -> None:
        up = self._make_upstream(srv, self._controller.current_proto())
        if up is None:
            self._op_label.setText(
                f'<span style="color:#ff9f0a;">{self._controller.current_proto()} 客户端启动失败</span>'
            )
            return
        if up.host in ("", "0.0.0.0"):
            self._op_label.setText('<span style="color:#ff9f0a;">该服务器暂无 IP</span>')
            return
        meta = {
            "id": srv["id"],
            "kind": "vultr",
            "host": srv["ip"],
            "port": srv["port"],  # 始终存 SOCKS5 端口，协议端口由 _make_upstream 动态取
            "user": srv["user"],
            "password": srv["password"],
            "name": srv["name"],
        }
        self._controller.set_active(up, meta, self._upstream_text(srv, up))
        self._populate_table()
        self._update_detect_btn()
        self._sync_domain(srv["id"], srv["ip"])

    def _clear_upstream(self) -> None:
        self._controller.clear_upstream()
        self._populate_table()
        self._update_detect_btn()
        self._log("已取消当前上游")

    def _update_detect_btn(self) -> None:
        """有当前上游时启用「检测连通性」，否则禁用。"""
        cache = config.get("active_upstream_cache") or {}
        self._detect_btn.setEnabled(bool(cache.get("host")))

    def _sync_domain(self, server_id: str, ip: str) -> None:
        """切换后把该服务器的子域名 DNS 指到新 IP。"""
        sub = domain.server_domain(server_id)
        if not sub:
            return

        def _do():
            domain.sync_server_dns(server_id, ip)
            return sub

        w = CallWorker(_do)
        w.done.connect(
            lambda s: self._op_label.setText(
                f'<span style="color:#32d74b;">域名已同步：{s} → {ip}</span>'
            )
        )
        w.failed.connect(
            lambda m: self._op_label.setText(f'<span style="color:#ff453a;">域名同步失败：{m}</span>')
        )
        self._workers.append(w)
        w.finished.connect(w.deleteLater)
        w.start()

    def _power(self, srv: dict, action: str) -> None:
        key = config.get("vultr_api_key", "")
        if not key:
            self._op_label.setText('<span style="color:#ff9f0a;">未配置 Vultr API 密钥</span>')
            return
        self._op_label.setText('<span style="color:#0a84ff;">执行中…</span>')

        def _do():
            api = VultrAPI(key)
            if action == "halt":
                api.halt_instance(srv["id"])
            else:
                api.start_instance(srv["id"])
            return True

        w = CallWorker(_do)
        w.done.connect(lambda _=None: self.refresh())
        w.failed.connect(lambda m: self._op_label.setText(f'<span style="color:#ff453a;">操作失败：{m}</span>'))
        self._workers.append(w)
        w.finished.connect(w.deleteLater)
        w.start()

    def _delete(self, srv: dict) -> None:
        from PySide6.QtWidgets import QMessageBox

        ret = QMessageBox.question(
            self,
            "确认删除",
            f'确定删除「{srv["name"]}」({srv["ip"]})？',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if ret != QMessageBox.StandardButton.Yes:
            return
        if srv["type"] == "manual":
            mlist = config.get("manual_servers", [])
            mid = srv["id"].split(":")[1]
            config.set_value("manual_servers", [m for m in mlist if m["id"] != mid])
            if config.get("active_upstream_id", "") == srv["id"]:
                self._controller.clear_upstream()
            self.refresh()
            return
        key = config.get("vultr_api_key", "")
        if not key:
            return
        self._op_label.setText('<span style="color:#0a84ff;">删除中…</span>')

        def _do():
            api = VultrAPI(key)
            api.delete_instance(srv["id"])
            return True

        w = CallWorker(_do)
        w.done.connect(lambda _=None: self._on_deleted(srv["id"]))
        w.failed.connect(lambda m: self._op_label.setText(f'<span style="color:#ff453a;">删除失败：{m}</span>'))
        self._workers.append(w)
        w.finished.connect(w.deleteLater)
        w.start()

    def _on_deleted(self, server_id: str) -> None:
        """删除服务器后清理 per-server 配置（域名/凭据/备注/名称/到期/中转/当前上游）。"""
        for key in ("server_domains", "server_configs", "server_notes", "server_names", "server_expiry"):
            m = dict(config.get(key, {}))
            if server_id in m:
                m.pop(server_id, None)
                config.set_value(key, m)
        relays = [r for r in config.get("relays", []) if r.get("server_id") != server_id]
        config.set_value("relays", relays)
        if config.get("active_upstream_id", "") == server_id:
            self._controller.clear_upstream()
        self._op_label.setText('<span style="color:#32d74b;">✅ 已删除服务器并清理配置</span>')
        self.refresh()

    def _check_expiry(self) -> None:
        """到期检查（每小时）：提前 3 天/1 天提醒，到期当天自动销毁（停止扣费）。"""
        from datetime import datetime
        today = datetime.now().date()
        expiries = config.get("server_expiry", {})
        notified = getattr(self, "_expiry_notified", set())
        for srv in getattr(self, "_servers", []):
            sid = srv.get("id", "")
            if srv.get("type") == "manual":
                continue
            if self._is_control(srv):
                continue  # 控制机（IPv6）不自动销毁
            exp_str = (expiries.get(sid, "") or "")[:10]
            if not exp_str:
                continue
            try:
                exp_date = datetime.strptime(exp_str, "%Y-%m-%d").date()
            except ValueError:
                continue
            days_left = (exp_date - today).days
            name = srv.get("name", sid)
            if days_left <= 0:
                # 已到期：自动销毁，停止扣费
                self._log(f"🔴 「{name}」已到期（{exp_str}），自动销毁停止扣费…")
                self._auto_destroy(srv)
                notified.discard(sid)
            elif days_left <= 3 and sid not in notified:
                notified.add(sid)
                if days_left == 1:
                    self._log(f"⚠️ 「{name}」明天到期（{exp_str}），到期将自动销毁，客户续费请先延长到期时间")
                else:
                    self._log(f"⚠️ 「{name}」还有 {days_left} 天到期（{exp_str}）")
        self._expiry_notified = notified

    def _auto_destroy(self, srv: dict) -> None:
        """到期自动销毁：直接销毁实例 + 清理配置（不弹窗，日志已记录）。"""
        key = config.get("vultr_api_key", "")
        if not key:
            self._log(f"❌ 自动销毁「{srv['name']}」失败：缺少 Vultr API Key")
            return

        def _do():
            api = VultrAPI(key)
            api.delete_instance(srv["id"])
            return True

        w = CallWorker(_do)
        w.done.connect(lambda _=None, sid=srv["id"]: self._on_deleted(sid))
        w.failed.connect(lambda m, n=srv["name"]: self._log(f"❌ 自动销毁「{n}」失败：{m}"))
        self._workers.append(w)
        w.finished.connect(w.deleteLater)
        w.start()

    def _log(self, msg: str) -> None:
        ts = time.strftime("%H:%M:%S")
        self._log_view.append(f"[{ts}] {msg}")
        sb = self._log_view.verticalScrollBar()
        sb.setValue(sb.maximum())
        try:
            from .users_page import global_echo

            global_echo(msg)
        except Exception:
            pass

    def _clear_log(self) -> None:
        self._log_view.clear()

    def _switch_ip(self, srv: dict) -> None:
        """切换IP：销毁这台服务器并重建，拿同国家新 IP。"""
        if srv["type"] == "manual":
            self._log("❌ 手工服务器无法自动切换IP（App 不知道其实例 ID）")
            self._op_label.setText('<span style="color:#ff9f0a;">手工服务器无法自动切换IP</span>')
            return

        # 弹窗选目标国家（默认当前国家）
        dlg = SwitchIpDialog(srv["region"], self, current_plan=srv.get("plan_id", ""))
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        target_region = dlg.selected_region()
        target_plan = dlg.selected_plan()
        sub = domain.server_domain(srv["id"])
        creds = gencreds.get_credentials(srv["id"])
        self._log(f"========== 开始切换IP：{srv['name']} ({srv['region']}) → 目标 {target_region} ==========")

        def _do(progress):
            stage_text = {
                "creating": "正在创建新机",
                "destroying": "正在销毁旧机",
                "booting": "新机启动中，正在安装代理软件",
                "testing": "代理软件已就绪，正在检测端口",
                "retrying": "端口检测失败，正在重建新机",
                "done": "切换成功",
                "failed": "切换失败",
            }
            progress("① 请求 Worker 销毁重建（同国家换区）...")
            result = api_client.admin_switch(resource_id=srv["id"], region=target_region, domain=sub, creds=creds, plan=target_plan)
            new_id = result.get("new_id", "")
            progress(f"② 已触发重建，新实例 {new_id[:8]}...，等待就绪...")
            for _ in range(72):  # 最多 6 分钟
                time.sleep(5)
                st = api_client.admin_switch_status()
                items = st.get("items", [])
                mine = next((it for it in items if it.get("old_id") == srv["id"]), None)
                if mine and mine.get("status") == "done":
                    new_ip = mine.get("new_ip", "")
                    label = mine.get("label", "")
                    region = mine.get("region", "")
                    progress(f"   🎉 切换完成，新 IP：{new_ip}（{region}）")
                    progress("③ 同步 DNS 到新 IP...")
                    _sync_dns_direct(sub, new_ip, progress)
                    progress("④ 验证 DNS 已指向新 IP...")
                    _verify_dns(sub, new_ip, progress)
                    return {"id": new_id, "main_ip": new_ip, "label": label, "region": region}
                if st.get("status") == "none" and len(items) == 0:
                    raise RuntimeError("切换状态丢失，请重试")
                # 阶段回显：显示当前卡在哪一步
                if mine:
                    stage = mine.get("stage", "")
                    note = mine.get("note", "")
                    progress(f"   ⏳ {stage_text.get(stage, stage or '处理中')}" + (f"：{note}" if note else ""))
                else:
                    progress("   ⏳ 排队等待中...")
            raise RuntimeError("切换超时（6 分钟），新实例可能仍在启动，请稍后刷新查看")

        w = ProgressWorker(_do)
        w.progress.connect(self._log)
        old_id = srv["id"]
        w.done.connect(lambda inst: self._on_switch_ip_done(inst, old_id))
        w.failed.connect(self._on_switch_ip_fail)
        self._workers.append(w)
        w.finished.connect(w.deleteLater)
        w.start()

    def _on_switch_ip_done(self, inst: dict, old_id: str = None) -> None:
        ip = inst.get("main_ip", "")
        self._log(f"⑧ 切换完成，新 IP：{ip}")
        # 迁移中转节点：旧实例 ID → 新实例 ID（中转需在「中转」里重新「应用到服务器」）
        if old_id:
            new_id = inst.get("id", "")
            relays = config.get("relays", [])
            changed = False
            for r in relays:
                if r.get("server_id") == old_id:
                    r["server_id"] = new_id
                    changed = True
            if changed:
                config.set_value("relays", relays)
                self._log(f"  已迁移中转节点到新实例，记得点「中转」→「应用到服务器」重新下发")
            # 切换前：优先用 Worker 销毁旧机前抓的流量，fallback 本地带宽缓存
            old_used = 0
            try:
                flow_data = api_client.list_switch_flow()
                for s in flow_data.get("switches", []):
                    if s.get("old_id") == old_id:
                        old_used = float(s.get("old_used_gb", 0) or 0)
                        break
            except Exception:
                pass
            if old_used <= 0:
                old_used = (self._bandwidth.get(old_id, {}) or {}).get("used_gb", 0)
            if old_used > 0:
                def _accum2(m):
                    m = dict(m or {})
                    # 累加（不是覆盖）：历史累计 + 这次旧实例出站
                    m[old_id] = float(m.get(old_id, 0) or 0) + old_used
                    return m
                config.mutate("server_cumulative_used", _accum2)
                self._log(f"  已用流量已记录：{old_used:.1f}G（切换后随服务器迁移）")
            # 迁移 per-server 配置（域名/凭据/备注/名称/到期/累计流量）从旧实例 ID 到新实例 ID
            for key in ("server_domains", "server_configs", "server_notes", "server_names", "server_expiry", "server_cumulative_used"):
                def _migrate(m):
                    m = dict(m or {})
                    if old_id in m:
                        m[new_id] = m.pop(old_id)
                    return m
                config.mutate(key, _migrate)
            sub = domain.server_domain(new_id)
            if sub:
                self._log(f"  域名已迁移：{sub}（等待 DNS 同步到新 IP {ip}）")
            else:
                self._log(f"  ⚠️ 该服务器未绑定域名，双击「域名」列可手动设置")
        self._op_label.setText(f'<span style="color:#32d74b;">已切换IP：{ip}</span>')
        srv = self._managed_row(inst)
        self._switch(srv)  # 设为当前 + 同步域名 + 测速
        # 证书已由 Worker cloud-init 自动处理（复用缓存或自动签发），服务端不再重复签
        sub = domain.server_domain(inst.get("id", ""))
        if sub:
            self._log(f"  证书由新机 cloud-init 自动处理（复用缓存或自动签发，域名 {sub}）")
        self.refresh()

    def _on_switch_ip_fail(self, msg: str) -> None:
        self._log(f"❌ 切换IP失败：{msg}")
        self._op_label.setText(f'<span style="color:#ff453a;">切换IP失败：{msg}</span>')
        self._log("已刷新服务器列表，请重新选择服务器后再切换")
        self.refresh()

    def _detect_blocked(self) -> None:
        cache = config.get("active_upstream_cache") or {}
        host = cache.get("host", "")
        if not host:
            self._log("❌ 无当前服务器")
            self._op_label.setText('<span style="color:#ff9f0a;">无当前服务器</span>')
            return
        sid = cache.get("id") or config.get("active_upstream_id", "")
        self._log(f"===== 检测节点 {host} 是否被墙 =====")
        self._op_label.setText(f'<span style="color:#0a84ff;">检测中：{host}…</span>')
        local_port = int(config.get("local_port", 1080))
        up_port = int(cache.get("port", 1080))

        def _do():
            creds = gencreds.get_credentials(sid)
            sub = domain.server_domain(sid)
            domain_ok = True
            domain_msg = ""
            if sub:
                resolved = domain.resolve_domain(sub)
                if not resolved:
                    domain_ok = False
                    domain_msg = f"域名 {sub} 尚未生效（DNS 未解析）"
                elif domain.is_fake_ip(resolved[0]):
                    # 解析到 fake-ip = 系统 DNS 被 VPN 劫持，不代表域名真的没生效
                    domain_ok = False
                    domain_msg = f"域名解析被 VPN 劫持（fake-ip {resolved[0]}），请关闭 Shadowrocket 后重测"
                elif host not in resolved:
                    domain_ok = False
                    domain_msg = f"域名 {sub} 未指向本机（解析到 {', '.join(resolved)}），DNS 还在传播"
            tunnel_active = check.vpn_tunnel_active()
            ssh_lat = check.tcp_latency(host, 22, 8)
            socks_lat = check.tcp_latency(host, up_port, 8)
            ss_lat = check.tcp_latency(host, int(creds.get("ss_port", 45678)), 8)
            reality_lat = check.tcp_latency(host, int(creds.get("reality_port", 443)), 8)
            hy_port = int(creds.get("hysteria_port", 8443))
            hy_open = check.udp_open(host, hy_port, 5)
            # Hysteria2 是 UDP，无法测准确往返，用 22 端口网络延迟近似
            hy_lat = ssh_lat if hy_open else None
            exit_ip = check.exit_ip_and_latency("127.0.0.1", local_port)
            e2e_lat = e2e.e2e_latencies(
                host=host,
                socks_user=creds.get("proxy_user", ""),
                socks_pass=creds.get("proxy_pass", ""),
                socks_port=up_port,
                ss_pass=creds.get("ss_password", ""),
                ss_port=int(creds.get("ss_port", 45678)),
                reality_uuid=creds.get("reality_uuid", ""),
                reality_public_key=creds.get("reality_public_key", ""),
                reality_short_id=creds.get("reality_short_id", ""),
                reality_sni=creds.get("reality_sni", "www.cloudflare.com"),
                reality_port=int(creds.get("reality_port", 443)),
                hy_pass=creds.get("hysteria_password", ""),
                hy_port=hy_port,
                domain=domain.server_domain(sid) or config.get("domain_name", ""),
            )
            return {"ssh_lat": ssh_lat, "socks_lat": socks_lat, "ss_lat": ss_lat,
                    "reality_lat": reality_lat, "hy_lat": hy_lat, "exit_ip": exit_ip,
                    "tunnel_active": tunnel_active, "e2e": e2e_lat,
                    "domain_ok": domain_ok, "domain_msg": domain_msg, "sub": sub}

        w = CallWorker(_do)
        w.done.connect(self._on_detect_done)
        w.failed.connect(lambda m: self._op_label.setText(f'<span style="color:#ff453a;">检测失败：{m}</span>'))
        self._workers.append(w)
        w.finished.connect(w.deleteLater)
        w.start()

    def _on_detect_done(self, r: dict) -> None:
        cache = config.get("active_upstream_cache") or {}
        host = cache.get("host", "")
        if r["ssh_lat"] is None:
            self._log(f"❌ {host} 22端口不通 —— 节点被墙或服务器异常，建议点「切换IP」")
            self._op_label.setText('<span style="color:#ff453a;">节点被墙，建议切换IP</span>')
            return
        if r.get("tunnel_active"):
            self._log("⚠️ 检测到 VPN 隧道（Shadowrocket 等）正在劫持直连流量，以下直连延迟可能为 0（假数据）")
            self._log("   请先关闭 Shadowrocket / 其它 VPN 再重新检测，否则直连延迟和域名解析结果都不可信")
        self._log(f"服务器可达 {host}（SSH {r['ssh_lat']}ms）")
        if not r.get("domain_ok", True):
            self._log(f"⚠️ {r.get('domain_msg', '域名未生效')}")
            self._log("   Hysteria2 依赖域名证书，域名未生效时它暂时不可用（SOCKS5/SS/Reality 用 IP 不受影响）")
            self._log("   建议等待 1-3 分钟（DNS 传播）后重新检测")
        marks = [
            ("SOCKS5", r["socks_lat"], False),
            ("SS", r["ss_lat"], False),
            ("Reality", r["reality_lat"], False),
            ("Hysteria2", r["hy_lat"], True),
        ]
        parts = []
        for name, lat, approx in marks:
            if lat is not None:
                tilde = "~" if approx else ""
                parts.append(f"✅{name} {tilde}{lat}ms")
            else:
                parts.append(f"❌{name}")
        self._log("端口连通（TCP 直连服务器，测端口是否开放+网络往返，非翻墙）：" + "  ".join(parts))
        if r["exit_ip"]:
            ip, lat = r["exit_ip"]
            self._log(f"代理出口延迟（通过本地代理访问的完整链路，这才是翻墙延迟）：{ip}（{lat}ms）")
            if all(lat is not None for _, lat, _ in marks):
                self._op_label.setText(f'<span style="color:#32d74b;">节点正常 · 四协议通 · 出口 {ip}</span>')
            else:
                self._op_label.setText('<span style="color:#ff9f0a;">部分协议端口不通</span>')
        else:
            self._log("⚠️ 直连通但代理转发失败")
            self._op_label.setText('<span style="color:#ff9f0a;">代理转发异常</span>')
        e2e_lat = r.get("e2e", {})
        if e2e_lat:
            self._log("端到端延迟（每个协议真实翻墙延迟）：")
            for proto in ("SOCKS5", "SS", "Reality", "Hysteria2"):
                lat = e2e_lat.get(proto)
                if lat is not None:
                    self._log(f"  {proto}: {lat}ms")
                elif proto == "Hysteria2" and not r.get("domain_ok", True):
                    self._log(f"  {proto}: ⏳ 域名未生效（等 DNS 生效后重测）")
                elif proto == "Hysteria2":
                    self._log(f"  {proto}: ❌ 不通（域名已解析但证书未签，双击「域名」列重新签发）")
                else:
                    self._log(f"  {proto}: ❌ 不通")

    def _open_share(self, srv: dict) -> None:
        relays = [r for r in config.get("relays", []) if r.get("server_id") == srv["id"]]
        # 优先用该服务器的子域名（换 IP 自动跟随），否则全局域名，否则 IP
        host = domain.server_domain(srv["id"]) or config.get("domain_name", "") or srv["ip"]
        NodeShareDialog(host, relays, gencreds.get_credentials(srv["id"]), self).exec()

    def _assign_user(self, srv: dict) -> None:
        """把当前服务器节点分配给某个用户（写入节点快照到 D1）。"""
        from .users_page import assign_dialog
        host = domain.server_domain(srv["id"]) or config.get("domain_name", "") or srv["ip"]
        creds = gencreds.get_credentials(srv["id"])
        port = int(creds.get("proxy_port", 1080))
        user = creds.get("proxy_user", "")
        password = creds.get("proxy_pass", "")
        ss_password = creds.get("ss_password", "")
        ss_port = int(creds.get("ss_port", 45678))
        ruuid = creds.get("reality_uuid", "")
        rpub = creds.get("reality_public_key", "")
        rsid = creds.get("reality_short_id", "")
        rsni = creds.get("reality_sni", "www.cloudflare.com")
        rport = int(creds.get("reality_port", 443))
        hy_password = creds.get("hysteria_password", "")
        hy_port = int(creds.get("hysteria_port", 8443))
        hy_mport = creds.get("hysteria_mport", "")

        node_data = {
            "host": host,
            "socks5": {"port": port, "username": user, "password": password},
            "ss": {"port": ss_port, "method": "aes-256-gcm", "password": ss_password},
            "reality": {"port": rport, "uuid": ruuid, "public_key": rpub, "short_id": rsid, "sni": rsni, "flow": "xtls-rprx-vision", "fp": "chrome"},
            "hysteria2": {"port": hy_port, "password": hy_password, "mport": hy_mport},
            "links": {
                "socks5": socks5_link(host, port, user, password, "SOCKS5"),
                "ss": ss_link(host, ss_port, "aes-256-gcm", ss_password, "SS"),
                "reality": vless_reality_link(host, rport, ruuid, "xtls-rprx-vision", rsni, "chrome", rpub, rsid, "Reality"),
                "hysteria2": hysteria2_link(host, hy_port, hy_password, hy_mport, "Hysteria2"),
            },
            "name": srv.get("name", ""),
            "location": region_label(srv.get("region", "")),
            "ip": srv.get("ip", ""),
            "domain": domain.server_domain(srv["id"]) or "",
            "bandwidth": self._bandwidth_text(self._bandwidth.get(srv["id"])),
            "created_at": (srv.get("date_created", "") or "")[:10],
            "expiry": config.get("server_expiry", {}).get(srv["id"], ""),
            "_creds": creds,  # 完整凭据（含 private_key/root_password），仅供 Worker 切换时用，客户端不显示
        }

        resource_expiry = config.get("server_expiry", {}).get(srv["id"], "")
        bw = self._bandwidth.get(srv["id"]) or {}
        total_quota_gb = float(bw.get("quota_gb", 0) or 0)
        used_gb = float(bw.get("used_gb", 0) or 0)
        assign_dialog(self, "vultr", srv["id"], node_data, resource_expiry, total_quota_gb=total_quota_gb, used_gb=used_gb)

    def _open_server_settings(self, srv: dict) -> None:
        old_sub = domain.server_domain(srv["id"])
        old_creds = gencreds.get_credentials(srv["id"])
        dlg = ServerSettingsDialog(srv, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        self._op_label.setText(f'<span style="color:#32d74b;">已保存「{srv["name"]}」的设置</span>')
        if getattr(dlg, "_rebuild", False):
            # 保存并重启：热更新服务器配置（IP 不变）
            self._confirm_and_restart(srv, old_creds)
            return
        new_sub = domain.server_domain(srv["id"])
        if new_sub and new_sub != old_sub and srv["ip"] not in ("", "0.0.0.0"):
            ip = srv["ip"]
            self._op_label.setText(f'<span style="color:#0a84ff;">子域名改为 {new_sub}，正在同步 DNS + 签证书…</span>')

            def _do():
                domain.sync_server_dns(srv["id"], ip)
                time.sleep(5)
                domain.sign_cert(ip, new_sub, password=gencreds.get_credentials(srv["id"]).get("root_password", ""))
                return new_sub

            w = CallWorker(_do)
            w.done.connect(lambda d: self._op_label.setText(f'<span style="color:#32d74b;">✅ 子域名 {d} 已生效</span>'))
            w.failed.connect(lambda m: self._op_label.setText(f'<span style="color:#ff453a;">子域名设置失败：{m}</span>'))
            self._workers.append(w)
            w.finished.connect(w.deleteLater)
            w.start()
        self.refresh()

    def _confirm_and_restart(self, srv: dict, old_creds: dict) -> None:
        """第二次确认后，SSH 热更新服务器配置（IP 不变）。"""
        ret = QMessageBox.question(
            self,
            "确认重启服务器",
            f"确定要重启服务器「{srv['name']}」（{srv['ip']}）吗？\n\n"
            "将把新的账号/密码/端口应用到服务器，IP 保持不变。\n"
            "重启期间代理会短暂中断（约几秒）。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if ret != QMessageBox.StandardButton.Yes:
            self._op_label.setText('<span style="color:#ff9f0a;">已取消重启</span>')
            return

        ip = srv["ip"]
        new_creds = gencreds.get_credentials(srv["id"])
        ssh_pw = old_creds.get("root_password", "")
        self._op_label.setText(f'<span style="color:#0a84ff;">正在重启「{srv["name"]}」应用新配置（IP 不变）…</span>')

        def _do():
            out, err = domain.apply_server_config(ip, new_creds, ssh_password=ssh_pw)
            return out + err

        w = CallWorker(_do)
        w.done.connect(lambda text: self._on_restart_done(srv, text))
        w.failed.connect(lambda m: self._op_label.setText(f'<span style="color:#ff453a;">重启失败：{m}</span>'))
        self._workers.append(w)
        w.finished.connect(w.deleteLater)
        w.start()

    def _on_restart_done(self, srv: dict, text: str) -> None:
        self._log(f"===== 重启服务器 {srv['name']} 完成 =====\n{text}")
        self._op_label.setText(f'<span style="color:#32d74b;">✅ 已重启「{srv["name"]}」（IP 不变）</span>')
        self.refresh()

    def _restart_server(self, srv: dict) -> None:
        """单纯重启服务器代理服务（gost/xray/hysteria），不换 IP、不改配置。"""
        ret = QMessageBox.question(
            self,
            "确认重启",
            f"确定要重启服务器「{srv['name']}」（{srv['ip']}）吗？\n\n"
            "仅重启代理服务（gost/xray/hysteria），IP 不变、配置不变。\n"
            "重启期间代理会短暂中断（约几秒）。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if ret != QMessageBox.StandardButton.Yes:
            return
        ip = srv["ip"]
        creds = gencreds.get_credentials(srv["id"])
        ssh_pw = creds.get("root_password", "")
        self._op_label.setText(f'<span style="color:#0a84ff;">正在重启「{srv["name"]}」…</span>')

        def _do():
            out, err = domain.restart_services(ip, ssh_password=ssh_pw)
            return out + err

        w = CallWorker(_do)
        w.done.connect(lambda text: self._on_restart_done(srv, text))
        w.failed.connect(lambda m: self._op_label.setText(f'<span style="color:#ff453a;">重启失败：{m}</span>'))
        self._workers.append(w)
        w.finished.connect(w.deleteLater)
        w.start()

    # ---------------- 新建 / 手工添加 ----------------
    def _open_create(self) -> None:
        key = config.get("vultr_api_key", "")
        if not key:
            self._op_label.setText('<span style="color:#ff9f0a;">请先在「设置」里配置 Vultr API 密钥</span>')
            return
        dlg = CreateServerDialog(config.get("regions", []), config.get("plans", []), self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        region, plan, subdomain, creds_override = dlg.values()
        self._op_label.setText(f'<span style="color:#0a84ff;">正在创建 {region} / {plan} …</span>')
        self._create_btn.setEnabled(False)

        def _do():
            api = VultrAPI(key)
            creds = gencreds.generate_credentials()
            creds.update(creds_override)
            ud = build_gost_user_data(
                creds["proxy_user"], creds["proxy_pass"], int(creds["proxy_port"]), creds
            )
            inst = api.create_instance(region, plan, config.get("label", "vultr-proxy-node"), ud)
            iid = inst.get("id", "")
            gencreds.save_credentials(iid, creds)
            if subdomain:
                domain.set_server_domain(iid, subdomain)
            else:
                domain.assign_subdomain(iid)
            for _ in range(60):
                time.sleep(5)
                d = api.get_instance(iid)
                ip = d.get("main_ip", "0.0.0.0")
                if ip and ip != "0.0.0.0":
                    return d
            return api.get_instance(iid)

        w = CallWorker(_do)
        w.done.connect(lambda d: self._on_created(d))
        w.failed.connect(self._on_create_fail)
        self._workers.append(w)
        w.finished.connect(w.deleteLater)
        w.start()

    def _on_created(self, inst: dict) -> None:
        self._create_btn.setEnabled(True)
        ip = inst.get("main_ip", "")
        iid = inst.get("id", "")
        sub = domain.server_domain(iid)
        self._op_label.setText(
            f'<span style="color:#32d74b;">已创建 {ip}，子域名 {sub}，正在同步 DNS + 签证书…</span>'
        )

        def _do():
            time.sleep(40)  # 等 cloud-init 装好 gost/xray/hysteria
            domain.sync_server_dns(iid, ip)
            time.sleep(5)
            # SSH 签证书：重试最多 3 分钟（cloud-init 装服务需要时间，SSH 可能未就绪）
            pw = gencreds.get_credentials(iid).get("root_password", "")
            for _ in range(18):
                try:
                    domain.sign_cert(ip, sub, password=pw)
                    return sub
                except Exception:
                    time.sleep(10)
            raise RuntimeError("签证书超时：SSH 未就绪（请稍后点「刷新」重试）")

        w = CallWorker(_do)
        w.done.connect(
            lambda s: self._op_label.setText(
                f'<span style="color:#32d74b;">✅ 服务器就绪，子域名 {s}（DNS+证书）</span>'
            )
        )
        w.failed.connect(
            lambda m: self._op_label.setText(
                f'<span style="color:#ff9f0a;">已创建，但域名配置未完成：{m}</span>'
            )
        )
        self._workers.append(w)
        w.finished.connect(w.deleteLater)
        w.start()
        self.refresh()

    def _on_create_fail(self, msg: str) -> None:
        self._create_btn.setEnabled(True)
        hint = ""
        if "limit" in msg.lower():
            hint = "（实例达到上限，先删除旧实例）"
        self._op_label.setText(f'<span style="color:#ff453a;">创建失败：{msg}{hint}</span>')

    def _open_manual(self) -> None:
        dlg = ManualServerDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        name, host, port, user, password = dlg.values()
        mlist = config.get("manual_servers", [])
        mlist.append(
            {"id": uuid.uuid4().hex[:8], "name": name, "host": host, "port": port, "user": user, "password": password}
        )
        config.set_value("manual_servers", mlist)
        self.refresh()


class CreateServerDialog(QDialog):
    """新建服务器：区域 + 套餐 + 子域名 + 完整凭据设置（留空自动生成）。"""

    def __init__(self, regions: list, plans: list, parent=None):
        super().__init__(parent)
        self.setWindowTitle("新建服务器")
        self.setMinimumWidth(620)
        self.resize(640, 700)

        outer = QVBoxLayout(self)
        outer.setSpacing(8)

        # 基础信息
        form = QFormLayout(self)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        # 国家 + 区域两级选择（中文显示，区域代码作为 data）
        self.country_combo = QComboBox()
        for c in COUNTRY_NAMES:
            self.country_combo.addItem(COUNTRY_NAMES[c], c)
        self.country_combo.currentIndexChanged.connect(self._fill_regions)
        form.addRow("国家", self.country_combo)
        self.region = QComboBox()
        form.addRow("区域（州/城市）", self.region)
        self._fill_regions()
        self.plan = QComboBox()
        for p in plans or ["vc2-1c-1gb", "vhf-1c-1gb", "vhp-1c-2gb"]:
            self.plan.addItem(PLAN_NAMES.get(p, p), p)
        form.addRow("套餐", self.plan)
        sub_row = QHBoxLayout()
        self.sub_combo = QComboBox()
        self.sub_combo.setEditable(True)
        self.sub_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.sub_combo.addItem("")
        sub_row.addWidget(self.sub_combo, 1)
        self.sub_refresh = QPushButton("拉取子域名")
        self.sub_refresh.clicked.connect(self._refresh_subs)
        sub_row.addWidget(self.sub_refresh)
        form.addRow("子域名", sub_row)
        tip = QLabel("子域名可直接输入，或下拉选已有；留空自动分配 nodeN.主域名。")
        tip.setStyleSheet("color: #98989d; font-size: 11px;")
        tip.setWordWrap(True)
        form.addRow("", tip)
        self.full_label = QLabel('<span style="color:#98989d;">（留空自动分配 nodeN.主域名）</span>')
        form.addRow("完整域名", self.full_label)
        self.sub_combo.editTextChanged.connect(self._update_full_domain)
        outer.addLayout(form)
        outer.addWidget(self._sep())

        # 凭据（留空自动生成）
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        lay = QVBoxLayout(content)
        lay.setSpacing(8)
        lay.setContentsMargins(0, 0, 8, 0)

        gen_row = QHBoxLayout()
        self.gen_all_btn = QPushButton("🔑 自动生成所有凭据")
        self.gen_all_btn.setProperty("compact", True)
        self.gen_all_btn.clicked.connect(self._generate_all)
        gen_row.addWidget(self.gen_all_btn)
        gen_row.addStretch(1)
        lay.addLayout(gen_row)
        lay.addWidget(self._hint("以下留空则自动生成；也可点上方按钮一键填充后再改。"))

        lay.addWidget(self._group_title("SOCKS5"))
        s5 = QFormLayout()
        s5.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.proxy_user = QLineEdit()
        s5.addRow("账号", self.proxy_user)
        self.proxy_pass = QLineEdit()
        s5.addRow("密码", self._pwd_row(self.proxy_pass))
        self.proxy_port = QSpinBox()
        self.proxy_port.setRange(1, 65535)
        self.proxy_port.setValue(1080)
        s5.addRow("端口", self.proxy_port)
        lay.addLayout(s5)
        lay.addWidget(self._sep())

        lay.addWidget(self._group_title("Shadowsocks"))
        ss = QFormLayout()
        ss.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.ss_pass = QLineEdit()
        ss.addRow("密码", self._pwd_row(self.ss_pass))
        self.ss_port = QSpinBox()
        self.ss_port.setRange(1, 65535)
        self.ss_port.setValue(45678)
        ss.addRow("端口", self.ss_port)
        lay.addLayout(ss)
        lay.addWidget(self._sep())

        lay.addWidget(self._group_title("VLESS+Reality"))
        rr = QFormLayout()
        rr.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.r_uuid = QLineEdit()
        rr.addRow("UUID", self.r_uuid)
        self.r_priv = QLineEdit()
        rr.addRow("私钥", self.r_priv)
        self.r_pub = QLineEdit()
        rr.addRow("公钥", self.r_pub)
        self.r_short = QLineEdit()
        rr.addRow("shortId", self.r_short)
        self.r_sni = QLineEdit("www.cloudflare.com")
        rr.addRow("SNI", self.r_sni)
        lay.addLayout(rr)
        gr_row = QHBoxLayout()
        self.gen_reality_btn = QPushButton("🔑 自动生成 Reality 密钥")
        self.gen_reality_btn.setProperty("compact", True)
        self.gen_reality_btn.clicked.connect(self._generate_reality)
        gr_row.addWidget(self.gen_reality_btn)
        gr_row.addStretch(1)
        lay.addLayout(gr_row)
        lay.addWidget(self._sep())

        lay.addWidget(self._group_title("Hysteria2"))
        hh = QFormLayout()
        hh.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.hy_pass = QLineEdit()
        hh.addRow("密码", self._pwd_row(self.hy_pass))
        self.hy_port = QSpinBox()
        self.hy_port.setRange(1, 65535)
        self.hy_port.setValue(8443)
        hh.addRow("端口", self.hy_port)
        self.hy_mport = QLineEdit("20000-20100")
        hh.addRow("端口跳跃", self.hy_mport)
        lay.addLayout(hh)
        lay.addWidget(self._sep())

        lay.addWidget(self._group_title("root 密码"))
        self.root_pass = QLineEdit()
        lay.addLayout(self._pwd_row(self.root_pass))

        scroll.setWidget(content)
        outer.addWidget(scroll, 1)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        outer.addWidget(btns)

    def _sep(self) -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("color: #3a3a3c;")
        return line

    def _group_title(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet("font-weight: 600; color: #0a84ff; margin-top: 4px;")
        return lbl

    def _hint(self, text: str) -> QLabel:
        lbl = QLabel("💡 " + text)
        lbl.setStyleSheet("color: #98989d; font-size: 11px;")
        lbl.setWordWrap(True)
        return lbl

    def _pwd_row(self, edit: QLineEdit) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(edit, 1)
        btn = QPushButton("生成")
        btn.setProperty("compact", True)
        btn.setFixedHeight(28)
        btn.clicked.connect(lambda _=False, e=edit: e.setText(gencreds.generate_password(16)))
        row.addWidget(btn)
        return row

    def _generate_reality(self) -> None:
        r = gencreds.generate_reality()
        self.r_uuid.setText(r["reality_uuid"])
        self.r_priv.setText(r["reality_private_key"])
        self.r_pub.setText(r["reality_public_key"])
        self.r_short.setText(r["reality_short_id"])

    def _generate_all(self) -> None:
        c = gencreds.generate_credentials()
        self.proxy_user.setText(c["proxy_user"])
        self.proxy_pass.setText(c["proxy_pass"])
        self.proxy_port.setValue(c["proxy_port"])
        self.ss_pass.setText(c["ss_password"])
        self.ss_port.setValue(c["ss_port"])
        self.root_pass.setText(c["root_password"])
        self.r_uuid.setText(c["reality_uuid"])
        self.r_priv.setText(c["reality_private_key"])
        self.r_pub.setText(c["reality_public_key"])
        self.r_short.setText(c["reality_short_id"])
        self.hy_pass.setText(c["hysteria_password"])
        self.hy_port.setValue(c["hysteria_port"])
        self.hy_mport.setText(c["hysteria_mport"])

    def _full_subdomain(self) -> str:
        """返回完整子域名（输入前缀自动拼主域名）。"""
        text = self.sub_combo.currentText().strip()
        zone = config.get("domain_name", "").strip()
        if not text:
            return ""
        if zone and "." in text and text.endswith("." + zone):
            return text
        if zone and "." not in text:
            return f"{text}.{zone}"
        return text

    def _update_full_domain(self, _text: str = "") -> None:
        """子域名输入实时同步显示完整域名（输入前缀自动拼主域名）。"""
        full = self._full_subdomain()
        if not full:
            self.full_label.setText('<span style="color:#98989d;">（留空自动分配 nodeN.主域名）</span>')
            return
        self.full_label.setText(f'<span style="color:#0a84ff;"><b>{full}</b></span>')

    def _refresh_subs(self) -> None:
        from ..cloudflare import get_zone_id, list_dns_records
        token = config.get("cf_token", "")
        domain_name = config.get("domain_name", "")
        if not token or not domain_name:
            return
        try:
            zid = config.get("zone_id", "") or get_zone_id(token, domain_name)
            recs = list_dns_records(token, zid)
            cur = self.sub_combo.currentText().strip()
            self.sub_combo.clear()
            self.sub_combo.addItem("")
            for rec in recs:
                self.sub_combo.addItem(rec["name"])
            if cur:
                idx = self.sub_combo.findText(cur)
                if idx >= 0:
                    self.sub_combo.setCurrentIndex(idx)
                else:
                    self.sub_combo.setEditText(cur)
        except Exception:
            pass

    def creds_override(self) -> dict:
        """返回用户填的非空凭据字段（留空的不覆盖，交给自动生成）。"""
        d: dict = {}
        for key, val in [
            ("proxy_user", self.proxy_user.text().strip()),
            ("proxy_pass", self.proxy_pass.text().strip()),
            ("proxy_port", self.proxy_port.value()),
            ("ss_password", self.ss_pass.text().strip()),
            ("ss_port", self.ss_port.value()),
            ("root_password", self.root_pass.text().strip()),
            ("reality_uuid", self.r_uuid.text().strip()),
            ("reality_private_key", self.r_priv.text().strip()),
            ("reality_public_key", self.r_pub.text().strip()),
            ("reality_short_id", self.r_short.text().strip()),
            ("reality_sni", self.r_sni.text().strip()),
            ("hysteria_password", self.hy_pass.text().strip()),
            ("hysteria_port", self.hy_port.value()),
            ("hysteria_mport", self.hy_mport.text().strip()),
        ]:
            if val not in ("", None):
                d[key] = val
        return d

    def _fill_regions(self) -> None:
        country = self.country_combo.currentData()
        self.region.clear()
        for r in COUNTRY_REGIONS.get(country, []):
            city = REGION_CITY.get(r, r)
            speed = REGION_SPEED.get(r, "中")
            self.region.addItem(f"{city}（{r}）· {speed}", r)

    def values(self):
        return self.region.currentData(), self.plan.currentData(), self._full_subdomain(), self.creds_override()


class ManualServerDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("手工添加服务器")
        form = QFormLayout(self)
        self.name = QLineEdit()
        self.name.setPlaceholderText("如 东京手动节点")
        form.addRow("名称", self.name)
        self.host = QLineEdit()
        self.host.setPlaceholderText("IP 或域名")
        form.addRow("地址", self.host)
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(int(config.get("proxy_port", 1080)))
        form.addRow("端口", self.port)
        self.user = QLineEdit(config.get("proxy_user", ""))
        form.addRow("账号", self.user)
        self.password = QLineEdit(config.get("proxy_pass", ""))
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("密码", self.password)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        form.addRow(btns)

    def values(self):
        return self.name.text().strip() or "手动节点", self.host.text().strip(), self.port.value(), self.user.text(), self.password.text()


class SwitchIpDialog(QDialog):
    """切换IP 前选目标国家 + 具体区域（州/城市）+ 套餐，标注大陆直连速度。"""

    def __init__(self, current_region: str, parent=None, current_plan: str = ""):
        super().__init__(parent)
        self.setWindowTitle("切换IP — 选择目标国家 + 区域 + 套餐")
        self.setMinimumWidth(400)
        form = QFormLayout(self)

        self.country_combo = QComboBox()
        cur = country_of(current_region)
        if cur and cur in COUNTRY_NAMES:
            self.country_combo.addItem(f"{COUNTRY_NAMES[cur]}（当前）", cur)
        for c in COUNTRY_NAMES:
            if c != cur:
                self.country_combo.addItem(COUNTRY_NAMES[c], c)
        self.country_combo.currentIndexChanged.connect(self._fill_regions)
        form.addRow("目标国家", self.country_combo)

        self.region_combo = QComboBox()
        form.addRow("目标区域（州/城市）", self.region_combo)

        # 套餐选择：管理员可在此换套餐（如 $5 1TB → $6 2TB）；仅服务端可用，客户端切换不能换套餐
        self.plan_combo = QComboBox()
        for p in config.get("plans", []) or []:
            self.plan_combo.addItem(PLAN_NAMES.get(p, p), p)
        if current_plan:
            idx = self.plan_combo.findData(current_plan)
            if idx >= 0:
                self.plan_combo.setCurrentIndex(idx)
        form.addRow("套餐（可换）", self.plan_combo)

        hint = QLabel("速度标签为中国大陆直连参考：快 = 西海岸/亚洲，慢 = 东海岸/欧洲")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #8e8e93;")
        form.addRow("", hint)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        form.addRow(btns)

        self._fill_regions()

    def _fill_regions(self) -> None:
        country = self.country_combo.currentData()
        self.region_combo.clear()
        for r in COUNTRY_REGIONS.get(country, []):
            city = REGION_CITY.get(r, r)
            speed = REGION_SPEED.get(r, "中")
            self.region_combo.addItem(f"{city}（{r}）· {speed}", r)

    def selected_region(self) -> str:
        return self.region_combo.currentData()

    def selected_plan(self) -> str:
        return self.plan_combo.currentData() or ""


class NodeShareDialog(QDialog):
    """显示各协议节点信息：每个参数详细列出 + 独立复制按钮 + 一键复制链接。"""

    def __init__(self, host: str, relays: list, creds: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("节点信息")
        self.setMinimumWidth(660)
        mono = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        header = QLabel(f'<span style="font-size:16px;font-weight:700;color:#f2f2f7;">{host}</span>')
        header.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(header)

        port = int(creds.get("proxy_port", 1080))
        user = creds.get("proxy_user", "")
        password = creds.get("proxy_pass", "")
        ss_password = creds.get("ss_password", "")
        ss_port = int(creds.get("ss_port", 45678))
        ruuid = creds.get("reality_uuid", "")
        rpub = creds.get("reality_public_key", "")
        rsid = creds.get("reality_short_id", "")
        rsni = creds.get("reality_sni", "www.cloudflare.com")
        rport = int(creds.get("reality_port", 443))
        hy_password = creds.get("hysteria_password", "")
        hy_port = int(creds.get("hysteria_port", 8443))
        hy_mport = creds.get("hysteria_mport", "")

        # SOCKS5
        if user and password:
            layout.addWidget(self._card(
                "SOCKS5",
                [
                    ("地址", host, False),
                    ("端口", str(port), False),
                    ("用户名", user, True),
                    ("密码", password, True),
                ],
                "复制 SOCKS5 链接",
                socks5_link(host, port, user, password, "SOCKS5"),
                mono,
            ))

        # Shadowsocks
        if ss_password:
            layout.addWidget(self._card(
                "Shadowsocks（SS）",
                [
                    ("地址", host, False),
                    ("端口", str(ss_port), False),
                    ("加密", "aes-256-gcm", False),
                    ("密码", ss_password, True),
                ],
                "复制 SS 链接",
                ss_link(host, ss_port, "aes-256-gcm", ss_password, "SS"),
                mono,
            ))

        # VLESS Reality
        if ruuid and rpub:
            layout.addWidget(self._card(
                "VLESS Reality",
                [
                    ("地址", host, False),
                    ("端口", str(rport), False),
                    ("UUID", ruuid, True),
                    ("流控", "xtls-rprx-vision", False),
                    ("SNI", rsni, False),
                    ("指纹", "chrome", False),
                    ("公钥", rpub, True),
                    ("Short ID", rsid, True),
                ],
                "复制 Reality 链接",
                vless_reality_link(host, rport, ruuid, "xtls-rprx-vision", rsni, "chrome", rpub, rsid, "Reality"),
                mono,
            ))

        # Hysteria2
        if hy_password:
            fields = [
                ("地址", host, False),
                ("端口", str(hy_port), False),
                ("密码", hy_password, True),
            ]
            if hy_mport:
                fields.append(("端口跳跃", hy_mport, False))
            layout.addWidget(self._card(
                "Hysteria2",
                fields,
                "复制 Hysteria2 链接",
                hysteria2_link(host, hy_port, hy_password, hy_mport, "Hysteria2"),
                mono,
            ))

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _card(self, section: str, fields: list, copy_label: str, copy_text: str, mono):
        card = QFrame()
        card.setProperty("card", True)
        lay = QVBoxLayout(card)
        lay.setSpacing(8)
        title = QLabel(f'<span style="color:#0a84ff;font-size:13px;font-weight:600;">{section}</span>')
        lay.addWidget(title)
        # 字段 2 列排，卡片更紧凑
        mid = (len(fields) + 1) // 2
        left_fields = fields[:mid]
        right_fields = fields[mid:]
        row = QHBoxLayout()
        row.setSpacing(16)
        lcol = QVBoxLayout()
        rcol = QVBoxLayout()
        lcol.setSpacing(6)
        rcol.setSpacing(6)
        for label, value, is_mono in left_fields:
            lcol.addLayout(self._kv(label, value, is_mono, mono))
        for label, value, is_mono in right_fields:
            rcol.addLayout(self._kv(label, value, is_mono, mono))
        row.addLayout(lcol, 1)
        row.addLayout(rcol, 1)
        lay.addLayout(row)
        btn = QPushButton(copy_label)
        btn.setProperty("primary", True)
        btn.clicked.connect(lambda _=False, t=copy_text, b=btn: self._copy(t, b, copy_label))
        lay.addWidget(btn)
        return card

    def _kv(self, label: str, value: str, is_mono: bool, mono):
        row = QHBoxLayout()
        row.setSpacing(8)
        lbl = QLabel(f'<span style="color:#98989d;">{label}</span>')
        lbl.setFixedWidth(76)
        val = QLabel(value)
        val.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        if is_mono:
            val.setFont(mono)
            val.setStyleSheet("color:#32d74b;")
        else:
            val.setStyleSheet("color:#f2f2f7;")
        cbtn = QPushButton("复制")
        cbtn.setProperty("compact", True)
        cbtn.setFixedHeight(24)
        cbtn.clicked.connect(lambda _=False, v=value, b=cbtn: self._copy(v, b, "复制"))
        row.addWidget(lbl)
        row.addWidget(val, 1)
        row.addWidget(cbtn)
        return row

    def _copy(self, text: str, btn=None, restore: str = "复制") -> None:
        QApplication.clipboard().setText(text)
        if btn is not None:
            btn.setText("已复制 ✓")
            QTimer.singleShot(1500, lambda: btn.setText(restore))


class RelayAddDialog(QDialog):
    """输入朋友的 SOCKS5，生成中转节点。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("添加中转节点")
        form = QFormLayout(self)
        self.name = QLineEdit()
        self.name.setPlaceholderText("如 朋友节点")
        form.addRow("名称", self.name)
        self.host = QLineEdit()
        self.host.setPlaceholderText("朋友的 SOCKS5 IP 或域名")
        form.addRow("目标地址", self.host)
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(1080)
        form.addRow("目标端口", self.port)
        self.user = QLineEdit()
        form.addRow("目标账号", self.user)
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("目标密码", self.password)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        form.addRow(btns)

    def values(self):
        return self.name.text().strip() or "中转", self.host.text().strip(), self.port.value(), self.user.text(), self.password.text()


class RelayManagerDialog(QDialog):
    """管理中转节点：添加/删除 + 应用到服务器。"""

    def __init__(self, server_id: str, server_ip: str, parent=None):
        super().__init__(parent)
        self.server_id = server_id
        self.server_ip = server_ip
        self.setWindowTitle(f"中转节点 — {server_ip}")
        self.setMinimumSize(640, 460)
        self._workers: list[CallWorker] = []
        layout = QVBoxLayout(self)

        hint = QLabel("填朋友的 SOCKS5，App 在你 Vultr 上生成一个加密 SS 中转，出口走朋友 IP，大陆直连且加密。")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #8e8e93;")
        layout.addWidget(hint)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["名称", "目标", "中转端口", "操作"])
        self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.table.verticalHeader().setVisible(False)
        hdr = self.table.horizontalHeader()
        hdr.setStretchLastSection(True)
        for c in range(3):
            hdr.setSectionResizeMode(c, QHeaderView.ResizeMode.Interactive)
        self.table.setColumnWidth(0, 110)
        self.table.setColumnWidth(1, 200)
        self.table.setColumnWidth(2, 80)
        layout.addWidget(self.table, 1)

        btns = QHBoxLayout()
        add_btn = QPushButton("添加中转")
        add_btn.setProperty("primary", True)
        add_btn.clicked.connect(self._add)
        btns.addWidget(add_btn)
        self._apply_btn = QPushButton("应用到服务器")
        self._apply_btn.clicked.connect(self._apply)
        btns.addWidget(self._apply_btn)
        btns.addStretch(1)
        layout.addLayout(btns)

        self._output = QTextEdit()
        self._output.setReadOnly(True)
        self._output.setFixedHeight(110)
        layout.addWidget(self._output)

        close_btn = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close_btn.rejected.connect(self.reject)
        layout.addWidget(close_btn)

        self._refresh()

    def _refresh(self) -> None:
        self.table.setRowCount(0)
        for r in config.get("relays", []):
            if r.get("server_id") != self.server_id:
                continue
            row = self.table.rowCount()
            self.table.insertRow(row)
            self._item(row, 0, r.get("name", ""))
            self._item(row, 1, f"{r.get('host', '')}:{r.get('port', '')}")
            self._item(row, 2, str(r.get("relay_port", "")))
            w = QWidget()
            lay = QHBoxLayout(w)
            lay.setContentsMargins(4, 2, 4, 2)
            lay.addStretch(1)
            btn = QPushButton("删除")
            btn.setProperty("compact", True)
            btn.setProperty("danger", True)
            btn.setFixedHeight(30)
            btn.clicked.connect(lambda _=False, rid=r["id"]: self._delete(rid))
            lay.addWidget(btn)
            lay.addStretch(1)
            self.table.setCellWidget(row, 3, w)

    def _item(self, r: int, c: int, text: str) -> None:
        it = QTableWidgetItem(str(text))
        it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self.table.setItem(r, c, it)

    def _add(self) -> None:
        dlg = RelayAddDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        name, host, port, user, password = dlg.values()
        if not host:
            return
        relays = config.get("relays", [])
        used = {int(x.get("relay_port", 0)) for x in relays}
        relays.append({
            "id": gen_relay_id(),
            "server_id": self.server_id,
            "name": name,
            "host": host,
            "port": port,
            "user": user,
            "password": password,
            "relay_port": next_relay_port(used),
            "relay_password": gen_relay_password(),
        })
        config.set_value("relays", relays)
        self._refresh()

    def _delete(self, rid: str) -> None:
        config.set_value("relays", [r for r in config.get("relays", []) if r["id"] != rid])
        self._refresh()

    def _apply(self) -> None:
        self._output.setPlainText("正在应用到服务器…\n")
        self._apply_btn.setEnabled(False)

        def _do():
            root = Path(__file__).resolve().parent.parent.parent
            script = root / "scripts" / "apply_relays.py"
            r = subprocess.run(
                ["uv", "run", "python", str(script), self.server_id, self.server_ip],
                capture_output=True, text=True, timeout=300, cwd=str(root),
            )
            return r.stdout + r.stderr

        w = CallWorker(_do)
        w.done.connect(self._on_applied)
        w.failed.connect(lambda m: self._on_applied(f"失败：{m}"))
        self._workers.append(w)
        w.finished.connect(w.deleteLater)
        w.start()

    def _on_applied(self, text: str) -> None:
        self._apply_btn.setEnabled(True)
        self._output.setPlainText(str(text))


class ServerSettingsDialog(QDialog):
    """单台服务器的独立设置：按协议分组，标注可改/需重建/建议默认。"""

    def __init__(self, srv: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f'服务器设置 · {srv["name"]}')
        self.setMinimumWidth(640)
        self.resize(660, 660)
        self.srv = srv
        self._workers: list[CallWorker] = []
        self._rebuild = False
        creds = gencreds.get_credentials(srv["id"])
        sub = domain.server_domain(srv["id"])

        outer = QVBoxLayout(self)
        outer.setSpacing(8)
        header = QLabel(f'服务器：<b>{srv["name"]}</b>（{srv["ip"]}）')
        outer.addWidget(header)

        # 滚动区域（内容多，底部按钮固定可见，不被屏幕裁掉）
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setSpacing(8)
        layout.setContentsMargins(0, 0, 8, 0)

        # ① 域名
        layout.addWidget(self._group_title("① 域名"))
        dom_row = QHBoxLayout()
        self.sub_combo = QComboBox()
        self.sub_combo.setEditable(True)
        self.sub_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.sub_combo.addItem(sub)
        self.sub_combo.setEditText(sub or "")
        dom_row.addWidget(self.sub_combo, 1)
        self.refresh_btn = QPushButton("拉取子域名")
        self.refresh_btn.clicked.connect(self._refresh_subdomains)
        dom_row.addWidget(self.refresh_btn)
        layout.addLayout(dom_row)
        self.sub_status = QLabel("✅ 改域名会立即同步 DNS + 重新签证书（选择已有或输入新子域名）")
        self.sub_status.setStyleSheet("color: #98989d; font-size: 11px;")
        self.sub_status.setWordWrap(True)
        layout.addWidget(self.sub_status)
        layout.addWidget(self._sep())

        # ② SOCKS5
        layout.addWidget(self._group_title("② SOCKS5（可改，改后需「切换IP」重建才生效）"))
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.proxy_user = QLineEdit(str(creds["proxy_user"]))
        form.addRow("账号", self.proxy_user)
        self.proxy_pass = QLineEdit(str(creds["proxy_pass"]))
        form.addRow("密码", self._pwd_row(self.proxy_pass))
        self.proxy_port = QSpinBox()
        self.proxy_port.setRange(1, 65535)
        self.proxy_port.setValue(int(creds["proxy_port"]))
        form.addRow("端口", self.proxy_port)
        layout.addLayout(form)
        layout.addWidget(self._hint("端口建议保留 1080；账号密码改了要重建"))
        layout.addWidget(self._sep())

        # ③ Shadowsocks
        layout.addWidget(self._group_title("③ Shadowsocks（可改，改后需重建）"))
        sform = QFormLayout()
        sform.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.ss_pass = QLineEdit(str(creds["ss_password"]))
        sform.addRow("密码", self._pwd_row(self.ss_pass))
        self.ss_port = QSpinBox()
        self.ss_port.setRange(1, 65535)
        self.ss_port.setValue(int(creds["ss_port"]))
        sform.addRow("端口", self.ss_port)
        layout.addLayout(sform)
        layout.addWidget(self._hint("加密 aes-256-gcm，端口建议 45678"))
        layout.addWidget(self._sep())

        # ④ Reality
        layout.addWidget(self._group_title("④ VLESS+Reality（可改，改后需重建）"))
        rform = QFormLayout()
        rform.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.r_uuid = QLineEdit(str(creds["reality_uuid"]))
        rform.addRow("UUID", self.r_uuid)
        self.r_priv = QLineEdit(str(creds["reality_private_key"]))
        rform.addRow("私钥", self.r_priv)
        self.r_pub = QLineEdit(str(creds["reality_public_key"]))
        rform.addRow("公钥", self.r_pub)
        self.r_short = QLineEdit(str(creds["reality_short_id"]))
        rform.addRow("shortId", self.r_short)
        self.r_sni = QLineEdit(str(creds["reality_sni"]))
        rform.addRow("SNI", self.r_sni)
        layout.addLayout(rform)
        gen_row = QHBoxLayout()
        self.gen_reality_btn = QPushButton("🔑 自动生成 Reality 密钥（UUID+私钥+公钥+shortId）")
        self.gen_reality_btn.setProperty("compact", True)
        self.gen_reality_btn.clicked.connect(self._generate_reality)
        gen_row.addWidget(self.gen_reality_btn)
        gen_row.addStretch(1)
        layout.addLayout(gen_row)
        layout.addWidget(self._hint("私钥/公钥必须配对才有效，点「自动生成」一键生成配对密钥；SNI 建议 www.cloudflare.com"))
        layout.addWidget(self._sep())

        # ⑤ Hysteria2
        layout.addWidget(self._group_title("⑤ Hysteria2（可改，改后需重建）"))
        hyform = QFormLayout()
        hyform.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.hy_pass = QLineEdit(str(creds["hysteria_password"]))
        hyform.addRow("密码", self._pwd_row(self.hy_pass))
        self.hy_port = QSpinBox()
        self.hy_port.setRange(1, 65535)
        self.hy_port.setValue(int(creds["hysteria_port"]))
        hyform.addRow("端口", self.hy_port)
        self.hy_mport = QLineEdit(str(creds["hysteria_mport"]))
        hyform.addRow("端口跳跃", self.hy_mport)
        layout.addLayout(hyform)
        layout.addWidget(self._hint("UDP 端口 8443 + 跳跃 20000-20100，建议保留默认"))
        layout.addWidget(self._sep())

        # ⑥ root 密码
        layout.addWidget(self._group_title("⑥ 服务器 root 密码（可改，改后需重建）"))
        self.root_pass = QLineEdit(str(creds["root_password"]))
        layout.addLayout(self._pwd_row(self.root_pass))
        layout.addWidget(self._sep())

        scroll.setWidget(content)
        outer.addWidget(scroll, 1)

        btns = QDialogButtonBox()
        self.rebuild_btn = btns.addButton("保存并重启", QDialogButtonBox.ButtonRole.AcceptRole)
        save_btn = btns.addButton("保存", QDialogButtonBox.ButtonRole.AcceptRole)
        cancel_btn = btns.addButton("取消", QDialogButtonBox.ButtonRole.RejectRole)
        self.rebuild_btn.clicked.connect(self._save_and_rebuild)
        save_btn.clicked.connect(self._save)
        cancel_btn.clicked.connect(self.reject)
        outer.addWidget(btns)

    def _sep(self) -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("color: #3a3a3c;")
        return line

    def _group_title(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet("font-weight: 600; color: #0a84ff; margin-top: 4px;")
        return lbl

    def _hint(self, text: str) -> QLabel:
        lbl = QLabel("💡 " + text)
        lbl.setStyleSheet("color: #98989d; font-size: 11px;")
        lbl.setWordWrap(True)
        return lbl

    def _refresh_subdomains(self) -> None:
        from ..cloudflare import get_zone_id, list_dns_records
        token = config.get("cf_token", "")
        domain_name = config.get("domain_name", "")
        cur_sub = domain.server_domain(self.srv["id"])
        target = cur_sub or domain_name
        if not token or not target:
            self.sub_status.setText('<span style="color:#ff9f0a;">请先在「设置」配置 Cloudflare 令牌和主域名</span>')
            return
        self.refresh_btn.setEnabled(False)
        self.sub_status.setText('<span style="color:#0a84ff;">拉取中…</span>')

        def _do():
            # 按该服务器当前子域名所属的一级域名反查 zone（支持多一级域名）
            zid = get_zone_id(token, target)
            return list_dns_records(token, zid)

        w = CallWorker(_do)
        w.done.connect(self._on_subdomains_loaded)
        w.failed.connect(lambda m: self._on_subdomains_fail(str(m)))
        self._workers.append(w)
        w.finished.connect(w.deleteLater)
        w.start()

    def _on_subdomains_loaded(self, recs: list) -> None:
        self.refresh_btn.setEnabled(True)
        cur = self.sub_combo.currentText().strip()
        self.sub_combo.clear()
        self.sub_combo.addItem("")
        for rec in recs:
            self.sub_combo.addItem(rec["name"])
        if cur:
            idx = self.sub_combo.findText(cur)
            if idx >= 0:
                self.sub_combo.setCurrentIndex(idx)
            else:
                self.sub_combo.setEditText(cur)
        self.sub_status.setText(
            f'<span style="color:#32d74b;">已加载 {len(recs)} 条 DNS 记录，可下拉选择或输入新子域名</span>'
        )

    def _on_subdomains_fail(self, msg: str) -> None:
        self.refresh_btn.setEnabled(True)
        self.sub_status.setText(f'<span style="color:#ff453a;">拉取失败：{msg}</span>')

    def _pwd_row(self, edit: QLineEdit) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(edit, 1)
        btn = QPushButton("生成")
        btn.setProperty("compact", True)
        btn.setFixedHeight(28)
        btn.clicked.connect(lambda _=False, e=edit: e.setText(gencreds.generate_password(16)))
        row.addWidget(btn)
        return row

    def _generate_reality(self) -> None:
        r = gencreds.generate_reality()
        self.r_uuid.setText(r["reality_uuid"])
        self.r_priv.setText(r["reality_private_key"])
        self.r_pub.setText(r["reality_public_key"])
        self.r_short.setText(r["reality_short_id"])

    def _save(self, rebuild: bool = False) -> None:
        creds = {
            "proxy_user": self.proxy_user.text().strip(),
            "proxy_pass": self.proxy_pass.text().strip(),
            "proxy_port": self.proxy_port.value(),
            "ss_password": self.ss_pass.text().strip(),
            "ss_port": self.ss_port.value(),
            "root_password": self.root_pass.text().strip(),
            "reality_uuid": self.r_uuid.text().strip(),
            "reality_private_key": self.r_priv.text().strip(),
            "reality_public_key": self.r_pub.text().strip(),
            "reality_short_id": self.r_short.text().strip(),
            "reality_sni": self.r_sni.text().strip(),
            "hysteria_password": self.hy_pass.text().strip(),
            "hysteria_port": self.hy_port.value(),
            "hysteria_mport": self.hy_mport.text().strip(),
        }
        gencreds.save_credentials(self.srv["id"], creds)
        sub = self.sub_combo.currentText().strip()
        if sub:
            domain.set_server_domain(self.srv["id"], sub)
        else:
            sd = dict(config.get("server_domains", {}))
            sd.pop(self.srv["id"], None)
            config.set_value("server_domains", sd)
        self._rebuild = rebuild
        self.accept()

    def _save_and_rebuild(self) -> None:
        self._save(rebuild=True)
