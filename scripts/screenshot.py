"""离屏渲染截图：验证 App 能启动并产出暗色界面（供人工核对）。

运行：QT_QPA_PLATFORM=offscreen uv run python scripts/screenshot.py
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QThread
from PySide6.QtWidgets import QApplication

from app.theme import apply_theme
from app.ui.main_window import MainWindow

app = QApplication(sys.argv)
apply_theme(app)
w = MainWindow()
w.resize(1280, 800)
w.show()
app.processEvents()
img = w.grab().toImage()
out = os.path.join(os.path.dirname(__file__), "..", "screenshot.png")
img.save(out)
print(f"saved {os.path.abspath(out)} size={img.width()}x{img.height()}")
w.shutdown()  # 优雅停机，避免 QThread 未结束导致 SIGABRT
app.processEvents()
QThread.msleep(600)  # 等 worker 线程彻底结束，避免 "QThread destroyed while running"
app.processEvents()
