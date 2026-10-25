"""Webshare-IP 管理页：加载 webshare 的代理服务器 + 静态住宅列表。"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import config, webshare
from .workers import CallWorker

_COLS = ["地址", "端口", "国家", "城市", "状态", "用户名", "密码"]


class WebsharePage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._workers: list[CallWorker] = []
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(16)

        title = QLabel("Webshare-IP")
        title.setStyleSheet("font-size: 20px; font-weight: 700;")
        root.addWidget(title)

        # API 密钥卡片
        key_card = QFrame()
        key_card.setProperty("card", True)
        kc = QVBoxLayout(key_card)
        kc.setContentsMargins(20, 16, 20, 16)
        kc.setSpacing(10)
        sec = QLabel("Webshare API")
        sec.setProperty("sectionTitle", True)
        kc.addWidget(sec)
        key_row = QHBoxLayout()
        self.api_key = QLineEdit(config.get("webshare_api_key", ""))
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key.setPlaceholderText("webshare API 密钥（dashboard → Account → API）")
        key_row.addWidget(self.api_key, 1)
        self.save_btn = QPushButton("保存密钥")
        self.save_btn.clicked.connect(self._save_key)
        key_row.addWidget(self.save_btn)
        self.refresh_btn = QPushButton("刷新列表")
        self.refresh_btn.setProperty("primary", True)
        self.refresh_btn.clicked.connect(self.refresh)
        key_row.addWidget(self.refresh_btn)
        kc.addLayout(key_row)
        self.status = QLabel("")
        kc.addWidget(self.status)
        root.addWidget(key_card)

        # 标签页：代理服务器 / 静态住宅
        self.tabs = QTabWidget()
        self.dc_table = self._make_table()
        self.res_table = self._make_table()
        self.tabs.addTab(self._wrap(self.dc_table), "代理服务器（机房）")
        self.tabs.addTab(self._wrap(self.res_table), "静态住宅（ISP）")
        root.addWidget(self.tabs, 1)

    def _make_table(self) -> QTableWidget:
        table = QTableWidget(0, len(_COLS))
        table.setHorizontalHeaderLabels(_COLS)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setAlternatingRowColors(True)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        return table

    @staticmethod
    def _wrap(table: QTableWidget) -> QWidget:
        w = QWidget()
        l = QVBoxLayout(w)
        l.setContentsMargins(0, 12, 0, 0)
        l.addWidget(table)
        return w

    def activate(self) -> None:
        if config.get("webshare_api_key", ""):
            self.refresh()

    def _save_key(self) -> None:
        key = self.api_key.text().strip()
        config.update(webshare_api_key=key)
        self.save_btn.setText("已保存 ✓")
        self.refresh()

    def refresh(self) -> None:
        key = self.api_key.text().strip() or config.get("webshare_api_key", "")
        if not key:
            self.status.setText('<span style="color:#ff9f0a;">请先填写 webshare API 密钥</span>')
            return
        self.status.setText('<span style="color:#0a84ff;">加载中…</span>')
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
        dc = data.get("proxy_servers", [])
        res = data.get("static_residential", [])
        self._fill(self.dc_table, dc, backbone=False)
        self._fill(self.res_table, res, backbone=True)
        self.tabs.setTabText(0, f"代理服务器（机房 · {len(dc)}）")
        self.tabs.setTabText(1, f"静态住宅（ISP · {len(res)}）")
        self.status.setText(
            f'<span style="color:#32d74b;">加载成功：代理服务器 {len(dc)} 个，静态住宅 {len(res)} 个</span>'
        )

    def _on_failed(self, msg: str) -> None:
        self.refresh_btn.setEnabled(True)
        self.status.setText(f'<span style="color:#ff453a;">失败：{msg}</span>')

    def _fill(self, table: QTableWidget, proxies: list, backbone: bool) -> None:
        table.setRowCount(0)
        table.setRowCount(len(proxies))
        for row, p in enumerate(proxies):
            addr = p.get("proxy_address") or ("p.webshare.io" if backbone else "")
            port = str(p.get("port", ""))
            country = p.get("country_code", "")
            city = p.get("city_name", "")
            valid = "✅" if p.get("valid") else "❌"
            user = p.get("username", "")
            pwd = p.get("password", "")
            for col, val in enumerate([addr, port, country, city, valid, user, pwd]):
                item = QTableWidgetItem(str(val))
                if col in (5, 6):  # 用户名/密码加 tooltip 便于复制查看
                    item.setToolTip(str(val))
                table.setItem(row, col, item)
