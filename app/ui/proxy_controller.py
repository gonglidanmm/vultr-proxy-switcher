"""共享本地代理控制器：本地代理 + 系统代理 + 协议 + 当前上游。

ProxyController = 纯逻辑（QObject，全局共享一份，管理 127.0.0.1:1080 本地代理）。
ProxyStatusCard = 状态卡 UI（QWidget，每个页面各建一个，绑定同一个 controller）。

当前上游是 Vultr 服务器（客户节点），
由页面通过 set_builder / set_active 接入。
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .. import config, sysproxy
from ..check import exit_ip_and_latency
from ..localproxy import LocalSocks5Proxy, Upstream
from ..proto_client import ProtoClient
from ..proxy_worker import ProxyWorker
from .workers import CallWorker


class ProxyController(QObject):
    """本地代理逻辑（全局共享一份）。通过信号通知所有状态卡 UI。"""

    proxy_status_changed = Signal(str)  # "running" / "stopped" / "error: ..."
    upstream_text_changed = Signal(str)  # 当前上游展示文本（HTML）
    exit_ip_changed = Signal(str)  # 出口 IP 检测结果文本（HTML）
    protocols_changed = Signal(list)  # 协议下拉框内容
    proto_changed = Signal(str)  # 当前协议名
    log_changed = Signal(str)  # 回显日志（页面监听并写入日志区）

    def __init__(self, parent=None):
        super().__init__(parent)
        self._workers: list[CallWorker] = []
        self._proxy = LocalSocks5Proxy(port=int(config.get("local_port", 1080)))
        self._proxy_worker: ProxyWorker | None = None
        self._proxy_running = False
        self._proto_client = ProtoClient()
        self._proto = config.get("active_proto", "SOCKS5")
        if self._proto in ("Reality", "Hysteria2"):
            self._proto = "SOCKS5"
            config.set_value("active_proto", "SOCKS5")
        self._sys_mode = "不代理"
        self._current_upstream: Upstream | None = None
        self._meta: dict | None = None
        self._builder = None  # fn(proto) -> Upstream | None
        self._upstream_text = "当前上游：无"
        self._exit_text = ""
        self._start_proxy()

    # ---------------- 页面接入 ----------------
    def set_builder(self, fn) -> None:
        self._builder = fn

    def set_protocols(self, protocols: list[str]) -> None:
        if self._proto not in protocols:
            self._proto = protocols[0] if protocols else "SOCKS5"
            config.set_value("active_proto", self._proto)
        self.protocols_changed.emit(list(protocols))
        self.proto_changed.emit(self._proto)

    def set_active(self, up: Upstream, meta: dict, text: str) -> None:
        self._current_upstream = up
        self._meta = meta
        self._proxy.set_upstream(up)
        config.update(active_upstream_id=meta.get("id"), active_upstream_cache=meta)
        self._upstream_text = text
        self.upstream_text_changed.emit(text)
        if not self._proxy_running:
            self._start_proxy()
        self.proxy_status_changed.emit("running" if self._proxy_running else "stopped")
        self._test_exit_ip()

    def clear_upstream(self) -> None:
        self._current_upstream = None
        self._meta = None
        self._proxy.set_upstream(None)
        config.update(active_upstream_id=None, active_upstream_cache=None)
        self._upstream_text = "当前上游：无"
        self.upstream_text_changed.emit(self._upstream_text)

    def set_sys_mode(self, index: int) -> None:
        """系统代理三档：0=不代理 1=智能分流 2=全局代理。"""
        port = int(config.get("local_port", 1080))
        if index == 0:
            self._sys_mode = "不代理"
            sysproxy.disable_proxy()
            self._exit_text = '<span style="color:#98989d;">未代理（选择「代理/全局代理」后自动检测）</span>'
            self.exit_ip_changed.emit(self._exit_text)
        elif index == 1:
            self._sys_mode = "代理"
            sysproxy.set_smart_proxy(port)
            self.log_changed.emit(f"系统代理「智能分流」· 协议 {self._proto}：国内直连，国外走 127.0.0.1:{port}")
            self._test_exit_ip()
        elif index == 2:
            self._sys_mode = "全局代理"
            sysproxy.set_global_proxy(port)
            self.log_changed.emit(f"系统代理「全局代理」· 协议 {self._proto}：所有流量走 127.0.0.1:{port}")
            self._test_exit_ip()

    def current_proto(self) -> str:
        return self._proto

    def sys_mode(self) -> str:
        return self._sys_mode

    def set_proto(self, proto: str) -> None:
        self._apply_proto(proto)

    def is_running(self) -> bool:
        return self._proxy_running

    def upstream_text(self) -> str:
        return self._upstream_text

    @property
    def proto_client(self) -> ProtoClient:
        return self._proto_client

    # ---------------- 生命周期 ----------------
    def _start_proxy(self) -> None:
        if self._proxy_worker is not None and self._proxy_worker.isRunning():
            return
        self._proxy_worker = ProxyWorker(self._proxy, self)
        self._proxy_worker.status.connect(self._on_proxy_status)
        self._proxy_worker.finished.connect(self._proxy_worker.deleteLater)
        self._proxy_worker.finished.connect(self._on_worker_finished)
        self._proxy_worker.start()

    def toggle_proxy(self) -> None:
        if self._proxy_running:
            self._stop_proxy()
        else:
            self._start_proxy()

    def _stop_proxy(self) -> None:
        if self._proxy_worker is not None:
            self._proxy_worker.request_stop()
            self._proxy_worker.wait(5000)

    def _on_worker_finished(self) -> None:
        self._proxy_worker = None

    def _on_proxy_status(self, status: str) -> None:
        if status == "running":
            self._proxy_running = True
        elif status == "stopped":
            self._proxy_running = False
        elif status.startswith("error"):
            self._proxy_running = False
        self.proxy_status_changed.emit(status)

    # ---------------- 协议 ----------------
    def _apply_proto(self, proto: str) -> None:
        old = self._proto
        self._proto = proto
        config.set_value("active_proto", proto)
        if self._builder is None:
            self.proto_changed.emit(proto)
            return
        up = self._builder(proto)
        if up is None:
            self._proto = old
            config.set_value("active_proto", old)
            self.proto_changed.emit(old)
            return
        self._current_upstream = up
        self._proxy.set_upstream(up)
        self.proto_changed.emit(proto)
        if self._sys_mode != "不代理":
            self._test_exit_ip()

    # ---------------- 出口检测 ----------------
    def _test_exit_ip(self) -> None:
        if self._sys_mode == "不代理":
            self._exit_text = '<span style="color:#98989d;">未代理（选择「代理/全局代理」后自动检测）</span>'
            self.exit_ip_changed.emit(self._exit_text)
            return
        self._exit_text = '<span style="color:#0a84ff;">正在检测出口 IP…</span>'
        self.exit_ip_changed.emit(self._exit_text)
        port = int(config.get("local_port", 1080))

        def _do():
            return exit_ip_and_latency("127.0.0.1", port)

        w = CallWorker(_do)
        w.done.connect(self._on_exit_ip)
        w.failed.connect(lambda m: self.exit_ip_changed.emit(f'<span style="color:#ff453a;">检测失败：{m}</span>'))
        self._workers.append(w)
        w.finished.connect(w.deleteLater)
        w.start()

    def _on_exit_ip(self, result) -> None:
        if result:
            ip, lat = result
            self._exit_text = (
                f'<span style="color:#98989d;">出口 IP：</span>'
                f'<span style="color:#32d74b;">{ip}</span>'
                f'<span style="color:#98989d;">（延迟 {lat} ms）</span>'
            )
            self.log_changed.emit(f"✅ 代理连通：出口 {ip}（延迟 {lat}ms）")
        else:
            self._exit_text = '<span style="color:#ff9f0a;">出口 IP 检测失败（gost 可能还在启动，稍后重试）</span>'
            self.log_changed.emit("❌ 代理测试失败：无法连通（检查协议/服务器是否正常）")
        self.exit_ip_changed.emit(self._exit_text)

    def shutdown(self) -> None:
        self._stop_proxy()
        self._proto_client.stop()
        import shiboken6
        for w in self._workers:
            if shiboken6.isValid(w) and w.isRunning():
                w.wait(2000)


class ProxyStatusCard(QWidget):
    """本地代理状态卡 UI（每个页面各建一个，绑定同一个 controller）。"""

    def __init__(self, controller: ProxyController, parent=None):
        super().__init__(parent)
        self._controller = controller
        self._build_ui()
        # 绑定信号
        controller.proxy_status_changed.connect(self._on_proxy_status)
        controller.upstream_text_changed.connect(self._upstream_label.setText)
        controller.exit_ip_changed.connect(self._exit_label.setText)
        controller.protocols_changed.connect(self._on_protocols)
        controller.proto_changed.connect(self._on_proto)
        # 初始化
        self._on_proxy_status("running" if controller.is_running() else "stopped")
        self._upstream_label.setText(controller.upstream_text())
        self._on_protocols(["SOCKS5", "SS", "Reality", "Hysteria2"])

    def _build_ui(self) -> None:
        card = QFrame()
        card.setProperty("card", True)
        sc = QVBoxLayout(card)
        sc.setContentsMargins(20, 16, 20, 16)
        sc.setSpacing(10)

        top = QHBoxLayout()
        sec = QLabel("本地代理")
        sec.setProperty("sectionTitle", True)
        top.addWidget(sec)
        top.addStretch(1)
        sc.addLayout(top)

        status_row = QHBoxLayout()
        self._proxy_status = QLabel("")
        status_row.addWidget(self._proxy_status)
        status_row.addStretch(1)
        self._toggle_btn = QPushButton("停止")
        self._toggle_btn.setProperty("compact", True)
        self._toggle_btn.clicked.connect(self._controller.toggle_proxy)
        status_row.addWidget(self._toggle_btn)
        sc.addLayout(status_row)

        sys_row = QHBoxLayout()
        sys_row.setSpacing(8)
        sys_row.addWidget(QLabel("系统代理"))
        self._sys_proxy_combo = QComboBox()
        self._sys_proxy_combo.addItems(["不代理", "代理（智能分流）", "全局代理"])
        self._sys_proxy_combo.currentIndexChanged.connect(self._controller.set_sys_mode)
        sys_row.addWidget(self._sys_proxy_combo)
        sys_row.addSpacing(16)
        sys_row.addWidget(QLabel("协议"))
        self._proto_combo = QComboBox()
        self._proto_combo.currentIndexChanged.connect(self._on_proto_index_changed)
        sys_row.addWidget(self._proto_combo)
        sys_row.addStretch(1)
        sc.addLayout(sys_row)

        self._upstream_label = QLabel("当前上游：无")
        sc.addWidget(self._upstream_label)
        self._exit_label = QLabel("")
        sc.addWidget(self._exit_label)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(card)

    def _on_proxy_status(self, status: str) -> None:
        port = config.get("local_port", 1080)
        if status == "running":
            self._proxy_status.setText(
                f'<span style="color:#32d74b;">● 运行中</span> '
                f'<span style="color:#98989d;">127.0.0.1:{port}</span>'
            )
            self._toggle_btn.setText("停止")
        else:
            self._proxy_status.setText('<span style="color:#ff453a;">● 已停止</span>')
            self._toggle_btn.setText("启动")

    def _on_protocols(self, protocols: list) -> None:
        self._proto_combo.blockSignals(True)
        self._proto_combo.clear()
        self._proto_combo.addItems(protocols)
        cur = self._controller.current_proto()
        if cur in protocols:
            self._proto_combo.setCurrentText(cur)
        self._proto_combo.blockSignals(False)

    def _on_proto(self, proto: str) -> None:
        self._proto_combo.blockSignals(True)
        idx = self._proto_combo.findText(proto)
        if idx >= 0:
            self._proto_combo.setCurrentIndex(idx)
        self._proto_combo.blockSignals(False)

    def _on_proto_index_changed(self, index: int) -> None:
        proto = self._proto_combo.itemText(index) if index >= 0 else ""
        if proto and proto != self._controller.current_proto():
            self._controller.set_proto(proto)
