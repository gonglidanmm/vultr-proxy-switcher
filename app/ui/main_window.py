"""主窗口：侧边栏导航 + 内容区。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
from .proxy_controller import ProxyController
from .servers_page import ServersPage
from .settings_page import SettingsPage
from .users_page import UsersPage
from .webshare_page import WebsharePage

_ASSETS = Path(__file__).resolve().parent.parent.parent / "assets"


class MainWindow(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Vultr 代理切换器")
        self._build()

    def _build(self) -> None:
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # 侧边栏
        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(220)
        sv = QVBoxLayout(sidebar)
        sv.setContentsMargins(0, 20, 0, 16)
        sv.setSpacing(0)

        logo_box = QVBoxLayout()
        logo_box.setContentsMargins(20, 0, 20, 20)
        logo_icon = QLabel("🛰️")
        logo_icon.setStyleSheet("font-size: 24px;")
        logo_title = QLabel("代理切换器")
        logo_title.setStyleSheet("font-size: 16px; font-weight: 700; color: #f2f2f7;")
        logo_sub = QLabel("多上游 IP 代理平台")
        logo_sub.setStyleSheet("font-size: 11px; color: #8e8e93;")
        logo_box.addWidget(logo_icon)
        logo_box.addWidget(logo_title)
        logo_box.addWidget(logo_sub)
        sv.addLayout(logo_box)

        self.nav = QListWidget()
        self.nav.setObjectName("nav")
        self.nav.setIconSize(QSize(20, 20))
        vultr_item = QListWidgetItem("Vultr-IP")
        vultr_item.setIcon(QIcon(str(_ASSETS / "vultr_icon.svg")))
        self.nav.addItem(vultr_item)
        self.nav.addItem(QListWidgetItem("Webshare-IP"))
        self.nav.addItem(QListWidgetItem("👥 用户管理"))
        self.nav.addItem(QListWidgetItem("⚙️  设置"))
        self.nav.setCurrentRow(0)
        self.nav.currentRowChanged.connect(self._on_nav)
        sv.addWidget(self.nav, 1)

        footer = QLabel(f"v{__version__}")
        footer.setContentsMargins(20, 12, 20, 0)
        footer.setStyleSheet("color: #6e6e73; font-size: 11px;")
        sv.addWidget(footer)
        outer.addWidget(sidebar)

        # 内容区
        self.stack = QStackedWidget()
        self.proxy_controller = ProxyController()
        self.servers_page = ServersPage(self.proxy_controller)
        self.webshare_page = WebsharePage()
        self.settings_page = SettingsPage()
        self.users_page = UsersPage()
        self.stack.addWidget(self.servers_page)
        self.stack.addWidget(self.webshare_page)
        self.stack.addWidget(self.users_page)
        self.stack.addWidget(self.settings_page)
        outer.addWidget(self.stack, 1)

    def _on_nav(self, row: int) -> None:
        self.stack.setCurrentIndex(row)
        if row == 0:
            self.servers_page.activate()
        elif row == 1:
            self.webshare_page.activate()
        elif row == 2:
            self.users_page.activate()

    def shutdown(self) -> None:
        self.servers_page.shutdown()
        self.proxy_controller.shutdown()
