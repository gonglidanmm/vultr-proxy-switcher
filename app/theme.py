"""暗色主题：Fusion 样式 + 暗色 QPalette + QSS。

macOS 上 PySide6 默认「macos」原生样式不遵循 QSS 背景色、也管不到
QListWidget/QTableWidget 视口底色，必须强制 Fusion 并设 QPalette 兜底。
"""

from PySide6.QtGui import QColor, QPalette

# 用户页和全局 QSS 共用的颜色。散落的十六进制往这里收。
TOKENS = {
    "bg": "#1c1c1e",
    "surface": "#18181a",
    "card": "#232326",
    "border": "#2e2e32",
    "border_quiet": "#262628",
    "stroke": "#3a3a3c",
    "text": "#f2f2f7",
    "muted": "#8e8e93",
    "faint": "#6c6c70",
    "accent": "#0a84ff",
    "success": "#30d158",
    "success_bg": "rgba(48,209,88,0.16)",
    "warning": "#ff9f0a",
    "warning_bg": "rgba(255,159,10,0.16)",
    "danger": "#ff453a",
    "danger_bg": "rgba(255,69,58,0.16)",
    "chip": "#2c2c2e",
    "zebra": "#202022",
    "white": "#ffffff",
}


def apply_dark_palette(app) -> None:
    p = QPalette()
    bg = QColor(TOKENS["bg"])
    panel = QColor(TOKENS["chip"])
    text = QColor(TOKENS["text"])
    accent = QColor(TOKENS["accent"])
    p.setColor(QPalette.ColorRole.Window, bg)
    p.setColor(QPalette.ColorRole.Base, bg)  # 关键：viewport 用这个
    p.setColor(QPalette.ColorRole.AlternateBase, QColor(TOKENS["zebra"]))
    p.setColor(QPalette.ColorRole.Text, text)
    p.setColor(QPalette.ColorRole.Button, panel)
    p.setColor(QPalette.ColorRole.ButtonText, text)
    p.setColor(QPalette.ColorRole.Highlight, accent)
    p.setColor(QPalette.ColorRole.HighlightedText, QColor(TOKENS["white"]))
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor(TOKENS["faint"]))
    app.setPalette(p)


def _qss() -> str:
    t = TOKENS
    return f"""
QWidget {{ background-color: {t['bg']}; color: {t['text']}; font-size: 13px; }}
QLabel {{ background-color: transparent; }}
QWidget#sidebar {{ background-color: {t['card']}; }}
QWidget#sidebar QWidget {{ background-color: transparent; }}

QListWidget#nav {{ background-color: transparent; border: none; }}
QListWidget#nav::item {{ padding: 10px 14px; border-left: 3px solid transparent; color: {t['muted']}; }}
QListWidget#nav::item:hover {{ background-color: rgba(255,255,255,0.05); color: {t['text']}; }}
QListWidget#nav::item:selected {{
    background-color: rgba(10,132,255,0.16);
    border-left: 3px solid {t['accent']};
    color: {t['white']}; font-weight: 500;
}}

QTableWidget {{ background-color: {t['bg']}; border: 1px solid {t['border']}; border-radius: 10px; gridline-color: transparent; font-size: 12px; }}
QTableWidget::item {{ padding: 2px 8px; border: none; border-bottom: 1px solid {t['border_quiet']}; }}
QTableWidget::item:alternate {{ background-color: {t['zebra']}; }}
QTableWidget::item:hover {{ background-color: rgba(255,255,255,0.04); }}
QTableWidget::item:selected {{ background-color: rgba(10,132,255,0.16); color: {t['white']}; }}
QHeaderView::section {{
    background-color: {t['card']}; color: {t['muted']}; border: none;
    border-bottom: 1px solid {t['stroke']}; padding: 6px 10px; font-weight: 500; font-size: 12px;
}}
QTableCornerButton::section {{ background-color: {t['card']}; border: none; }}

QPushButton {{
    background-color: {t['chip']}; color: {t['text']}; border: 1px solid {t['stroke']};
    border-radius: 6px; padding: 6px 12px;
}}
QPushButton:hover {{ background-color: {t['stroke']}; }}
QPushButton:pressed {{ background-color: {t['card']}; }}
QPushButton:disabled {{ color: {t['faint']}; border-color: {t['chip']}; }}
QPushButton[primary="true"] {{
    background-color: {t['accent']};
    border: none; color: {t['white']}; font-weight: 600;
}}
QPushButton[primary="true"]:hover {{ background-color: {t['accent']}; }}
QPushButton[danger="true"] {{ background-color: #3a1e1e; border: 1px solid #7a2e2e; color: #ff8a80; }}
QPushButton[danger="true"]:hover {{ background-color: #4a2424; }}
QPushButton[compact="true"] {{ padding: 4px 10px; font-size: 12px; }}
QPushButton[chip="true"] {{
    background-color: {t['chip']};
    color: {t['muted']};
    border: 1px solid {t['stroke']};
    border-radius: 11px;
    padding: 0 10px;
    min-height: 22px;
    max-height: 22px;
    font-size: 12px;
    font-weight: 500;
}}
QPushButton[chip="true"]:hover {{ color: {t['text']}; border-color: {t['muted']}; background-color: {t['chip']}; }}
QPushButton[chip="true"]:checked {{
    background-color: {t['accent']};
    border: 1px solid {t['accent']};
    color: {t['white']};
    font-weight: 600;
}}
QPushButton[chip="true"]:checked:hover {{
    background-color: {t['accent']};
    border-color: {t['accent']};
    color: {t['white']};
}}

QLineEdit, QSpinBox, QComboBox {{
    background-color: {t['chip']}; border: 1px solid {t['stroke']}; border-radius: 6px; padding: 4px 8px; color: {t['text']};
}}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus {{ border-color: {t['accent']}; }}
QComboBox QAbstractItemView {{ background-color: {t['chip']}; border: 1px solid {t['stroke']}; selection-background-color: {t['accent']}; }}

QFrame[card="true"] {{ background-color: {t['card']}; border: 1px solid {t['border']}; border-radius: 10px; }}
QWidget#detailPane {{ background-color: {t['surface']}; }}
QLabel[sectionTitle="true"] {{ color: {t['text']}; font-size: 13px; font-weight: 600; }}
QLabel[statValue="true"] {{ color: {t['text']}; font-size: 26px; font-weight: 700; }}
QLabel[statName="true"] {{ color: {t['muted']}; font-size: 12px; }}

QProgressBar {{ background-color: {t['chip']}; border: none; border-radius: 2px; }}
QProgressBar::chunk {{ background-color: {t['accent']}; border-radius: 2px; }}

QTabBar {{ background: transparent; }}
QTabBar::tab {{
    background: transparent; color: {t['muted']};
    padding: 8px 18px; border: none; border-bottom: 2px solid transparent;
    margin-right: 4px; font-size: 13px;
}}
QTabBar::tab:hover {{ color: {t['text']}; }}
QTabBar::tab:selected {{ color: {t['white']}; border-bottom: 2px solid {t['accent']}; font-weight: 600; }}

QTextEdit, QPlainTextEdit {{
    background-color: {t['bg']}; border: 1px solid {t['border']}; border-radius: 8px;
    padding: 8px; color: {t['muted']}; font-size: 12px;
}}
QSplitter::handle {{ background-color: {t['border']}; }}
QSplitter::handle:horizontal {{ width: 1px; }}
"""


DARK_QSS = _qss()


def apply_theme(app) -> None:
    app.setStyle("Fusion")
    apply_dark_palette(app)
    app.setStyleSheet(DARK_QSS)
