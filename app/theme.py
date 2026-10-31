"""暗色主题：Fusion 样式 + 暗色 QPalette + QSS。

macOS 上 PySide6 默认「macos」原生样式不遵循 QSS 背景色、也管不到
QListWidget/QTableWidget 视口底色，必须强制 Fusion 并设 QPalette 兜底。
"""

from PySide6.QtGui import QColor, QPalette


def apply_dark_palette(app) -> None:
    p = QPalette()
    bg = QColor("#1c1c1e")
    panel = QColor("#2c2c2e")
    text = QColor("#f2f2f7")
    accent = QColor("#0a84ff")
    p.setColor(QPalette.ColorRole.Window, bg)
    p.setColor(QPalette.ColorRole.Base, bg)  # 关键：viewport 用这个
    p.setColor(QPalette.ColorRole.AlternateBase, QColor("#202022"))
    p.setColor(QPalette.ColorRole.Text, text)
    p.setColor(QPalette.ColorRole.Button, panel)
    p.setColor(QPalette.ColorRole.ButtonText, text)
    p.setColor(QPalette.ColorRole.Highlight, accent)
    p.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor("#6e6e73"))
    app.setPalette(p)


# 全局 QSS。颜色沿用 Apple 暗色板。
DARK_QSS = """
QWidget { background-color: #1c1c1e; color: #f2f2f7; font-size: 13px; }
QLabel { background-color: transparent; }
QWidget#sidebar { background-color: #232326; }
QWidget#sidebar QWidget { background-color: transparent; }

QListWidget#nav { background-color: transparent; border: none; }
QListWidget#nav::item { padding: 10px 14px; border-left: 3px solid transparent; color: #98989d; }
QListWidget#nav::item:hover { background-color: rgba(255,255,255,0.05); color: #e5e5ea; }
QListWidget#nav::item:selected {
    background-color: rgba(10,132,255,0.16);
    border-left: 3px solid #0a84ff;
    color: #ffffff; font-weight: 500;
}

QTableWidget { background-color: #1c1c1e; border: 1px solid #3a3a3c; border-radius: 8px; gridline-color: #2c2c2e; font-size: 12px; }
QTableWidget::item { padding: 6px 10px; border: none; }
/* 勾选框：暗色下加明确边框/背景，否则深色方框在深色底上几乎不可见 */
QTableView::indicator, QTableWidget::indicator {
    width: 16px; height: 16px;
    border: 2px solid #6e6e73;
    border-radius: 3px;
    background: #2c2c2e;
}
QTableView::indicator:checked, QTableWidget::indicator:checked {
    background: #0a84ff;
    border-color: #0a84ff;
}
QHeaderView::section {
    background-color: #232326; color: #98989d; border: none;
    border-bottom: 1px solid #3a3a3c; padding: 8px 10px; font-weight: 500; font-size: 13px;
}
QTableCornerButton::section { background-color: #232326; border: none; }

QPushButton {
    background-color: #2c2c2e; color: #f2f2f7; border: 1px solid #3a3a3c;
    border-radius: 6px; padding: 8px 16px;
}
QPushButton:hover { background-color: #3a3a3c; }
QPushButton:pressed { background-color: #232326; }
QPushButton:disabled { color: #6e6e73; border-color: #2c2c2e; }
QPushButton[primary="true"] {
    background-color: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #0a84ff, stop:1 #3ea6ff);
    border: none; color: #ffffff; font-weight: 500;
}
QPushButton[primary="true"]:hover { background-color: #0a84ff; }
QPushButton[danger="true"] { background-color: #3a1e1e; border: 1px solid #7a2e2e; color: #ff8a80; }
QPushButton[danger="true"]:hover { background-color: #4a2424; }
QPushButton[compact="true"] { padding: 5px 10px; font-size: 12px; }

QLineEdit, QSpinBox, QComboBox {
    background-color: #2c2c2e; border: 1px solid #3a3a3c; border-radius: 6px; padding: 7px 10px; color: #f2f2f7;
}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus { border-color: #0a84ff; }
QComboBox QAbstractItemView { background-color: #2c2c2e; border: 1px solid #3a3a3c; selection-background-color: #0a84ff; }

QFrame[card="true"] { background-color: #232326; border: 1px solid #2c2c2e; border-radius: 12px; }
QLabel[sectionTitle="true"] { color: #0a84ff; font-size: 13px; font-weight: 600; }
QLabel[statValue="true"] { color: #f2f2f7; font-size: 26px; font-weight: 700; }
QLabel[statName="true"] { color: #8e8e93; font-size: 12px; }

QProgressBar { background-color: #2c2c2e; border: none; border-radius: 3px; }
QProgressBar::chunk { background-color: #0a84ff; border-radius: 3px; }

QTabBar { background: transparent; }
QTabBar::tab {
    background: transparent; color: #98989d;
    padding: 8px 18px; border: none; border-bottom: 2px solid transparent;
    margin-right: 4px; font-size: 13px;
}
QTabBar::tab:hover { color: #e5e5ea; }
QTabBar::tab:selected { color: #ffffff; border-bottom: 2px solid #0a84ff; font-weight: 600; }

QTextEdit {
    background-color: #1c1c1e; border: 1px solid #3a3a3c; border-radius: 8px;
    padding: 8px; color: #98989d; font-size: 12px;
}
"""


def apply_theme(app) -> None:
    app.setStyle("Fusion")
    apply_dark_palette(app)
    app.setStyleSheet(DARK_QSS)
