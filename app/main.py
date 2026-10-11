"""入口：python -m app.main"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from .theme import apply_theme
from .ui.main_window import MainWindow

_ASSETS = Path(__file__).resolve().parent.parent / "assets"


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("vultr-proxy-switcher")
    _icon = _ASSETS / "icon.icns" if sys.platform == "darwin" else _ASSETS / "icon_512.png"
    app.setWindowIcon(QIcon(str(_icon)))
    apply_theme(app)

    window = MainWindow()
    window.setMinimumSize(900, 640)
    # 打开即最大化 + 锁定（移除最大化按钮，无法退出最大化态）
    window.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, False)
    window.showMaximized()
    app.aboutToQuit.connect(window.shutdown)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
