"""暗色主题：Fusion 样式 + 暗色 QPalette + QSS。

macOS 上 PySide6 默认「macos」原生样式不遵循 QSS 背景色、也管不到
QListWidget/QTableWidget 视口底色，必须强制 Fusion 并设 QPalette 兜底。
"""

from PySide6.QtGui import QColor, QPalette

# 五级灰 + 字号/圆角/间距。QSS 不支持变量，这里插值进去。
# bg/card/chip 是旧名字，分别指向 window / surface / surface2，避免调用方散落色值。
TOKENS = {
    "window": "#141418",
    "sidebar": "#1c1c22",
    "surface": "#24242c",
    "surface2": "#2e2e38",
    "border": "#3c3c48",
    "bg": "#141418",
    "card": "#24242c",
    "chip": "#2e2e38",
    "border_quiet": "#2a2a32",
    "stroke": "#4a4a56",
    "text": "#f4f4f5",
    "muted": "#9a9aa3",
    "faint": "#6e6e78",
    "accent": "#3b82f6",
    "success": "#34d399",
    "success_bg": "rgba(52,211,153,0.16)",
    "warning": "#f5a524",
    "warning_bg": "rgba(245,165,36,0.16)",
    "danger": "#ff453a",
    "danger_bg": "rgba(255,69,58,0.16)",
    "zebra": "#1a1a20",
    "white": "#ffffff",
    "radius_sm": 8,
    "radius": 12,
    "space": 12,
    "space_lg": 24,
    "font_title": 28,
    "font_metric": 26,
    "font_body": 13,
    "font_small": 12,
    "rail": 320,
}


def apply_dark_palette(app) -> None:
    p = QPalette()
    bg = QColor(TOKENS["window"])
    panel = QColor(TOKENS["surface2"])
    text = QColor(TOKENS["text"])
    accent = QColor(TOKENS["accent"])
    p.setColor(QPalette.ColorRole.Window, bg)
    p.setColor(QPalette.ColorRole.Base, bg)
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
    r = t["radius"]
    rs = t["radius_sm"]
    return f"""
QWidget {{ background-color: {t['window']}; color: {t['text']}; font-size: {t['font_body']}px; }}
QLabel {{ background-color: transparent; }}
QWidget#sidebar {{ background-color: {t['sidebar']}; }}
QWidget#sidebar QWidget {{ background-color: transparent; }}
QWidget#userRail {{ background-color: {t['sidebar']}; border-right: 1px solid {t['border']}; }}
QWidget#railBody {{ background-color: {t['sidebar']}; }}
QWidget#workspace {{ background-color: {t['window']}; }}
QWidget#chipRow {{ background-color: transparent; }}

QListWidget#nav {{ background-color: transparent; border: none; }}
QListWidget#nav::item {{ padding: 10px 14px; border-left: 3px solid transparent; color: {t['muted']}; }}
QListWidget#nav::item:hover {{ background-color: rgba(255,255,255,0.05); color: {t['text']}; }}
QListWidget#nav::item:selected {{
    background-color: rgba(59,130,246,0.16);
    border-left: 3px solid {t['accent']};
    color: {t['white']}; font-weight: 500;
}}

QFrame#userRow {{
    background-color: transparent;
    border: none;
    border-left: 2px solid transparent;
    border-radius: {rs}px;
}}
QFrame#userRow:hover {{ background-color: rgba(255,255,255,0.05); }}
QFrame#userRow[selected="true"] {{
    background-color: {t['surface2']};
    border-left: 2px solid {t['accent']};
}}
QFrame#userRow[selected="true"]:hover {{ background-color: {t['surface2']}; }}
QFrame#lineRow {{
    background-color: {t['surface2']};
    border: 1px solid {t['border']};
    border-radius: {r}px;
}}
QFrame#lineRow[expired="true"] {{
    background-color: {t['window']};
    border-color: {t['border_quiet']};
}}
QFrame[card="true"] {{
    background-color: {t['surface']};
    border: 1px solid {t['border']};
    border-radius: {r}px;
}}

QScrollArea#railScroll, QScrollArea#chipScroll, QScrollArea#lineScroll {{
    background-color: transparent; border: none;
}}
QScrollArea#chipScroll QScrollBar:horizontal {{ height: 6px; background: transparent; margin: 0; }}
QScrollArea#chipScroll QScrollBar::handle:horizontal {{ background: {t['stroke']}; border-radius: 3px; min-width: 24px; }}
QScrollArea#chipScroll QScrollBar::add-line:horizontal, QScrollArea#chipScroll QScrollBar::sub-line:horizontal {{ width: 0; height: 0; }}
QScrollArea#railScroll QScrollBar:vertical, QScrollArea#lineScroll QScrollBar:vertical {{
    width: 8px; background: transparent; margin: 2px;
}}
QScrollArea#railScroll QScrollBar::handle:vertical, QScrollArea#lineScroll QScrollBar::handle:vertical {{
    background: {t['stroke']}; border-radius: 4px; min-height: 24px;
}}
QScrollArea#railScroll QScrollBar::add-line:vertical, QScrollArea#railScroll QScrollBar::sub-line:vertical,
QScrollArea#lineScroll QScrollBar::add-line:vertical, QScrollArea#lineScroll QScrollBar::sub-line:vertical {{
    height: 0; width: 0;
}}

QTableWidget {{ background-color: {t['window']}; border: 1px solid {t['border']}; border-radius: {r}px; gridline-color: transparent; font-size: {t['font_small']}px; }}
QTableWidget::item {{ padding: 2px 8px; border: none; border-bottom: 1px solid {t['border_quiet']}; }}
QTableWidget::item:alternate {{ background-color: {t['zebra']}; }}
QTableWidget::item:hover {{ background-color: rgba(255,255,255,0.04); }}
QTableWidget::item:selected {{ background-color: rgba(59,130,246,0.16); color: {t['white']}; }}
QHeaderView::section {{
    background-color: {t['surface']}; color: {t['muted']}; border: none;
    border-bottom: 1px solid {t['border']}; padding: 6px 10px; font-weight: 600; font-size: {t['font_small']}px;
}}
QTableCornerButton::section {{ background-color: {t['surface']}; border: none; }}

QPushButton {{
    background-color: {t['surface2']}; color: {t['text']}; border: 1px solid {t['border']};
    border-radius: {rs}px; padding: 0 14px; min-height: 34px; font-weight: 600;
}}
QPushButton:hover {{ background-color: {t['stroke']}; }}
QPushButton:pressed {{ background-color: {t['surface']}; }}
QPushButton:disabled {{ color: {t['faint']}; border-color: {t['border']}; }}
QPushButton[primary="true"] {{
    background-color: {t['accent']};
    border: none; color: {t['white']}; font-weight: 600;
}}
QPushButton[primary="true"]:hover {{ background-color: {t['accent']}; }}
QPushButton[danger="true"] {{ background-color: #3a1e1e; border: 1px solid #7a2e2e; color: #ff8a80; }}
QPushButton[danger="true"]:hover {{ background-color: #4a2424; }}
QPushButton[compact="true"] {{ min-height: 30px; padding: 0 12px; font-size: {t['font_small']}px; }}
QPushButton[iconbtn="true"] {{
    min-width: 34px; max-width: 34px; min-height: 34px; max-height: 34px;
    padding: 0; font-size: 18px;
}}
QPushButton[chip="true"] {{
    background-color: transparent;
    color: {t['muted']};
    border: 1px solid {t['stroke']};
    border-radius: 11px;
    padding: 0 8px;
    min-height: 22px;
    max-height: 22px;
    min-width: 0;
    font-size: {t['font_small']}px;
    font-weight: 600;
}}
QPushButton[chip="true"]:hover {{ color: {t['text']}; border-color: {t['muted']}; background-color: rgba(255,255,255,0.04); }}
QPushButton[chip="true"]:checked {{
    background-color: {t['accent']};
    border: 1px solid {t['accent']};
    color: {t['white']};
}}
QPushButton[chip="true"]:checked:hover {{
    background-color: {t['accent']};
    border-color: {t['accent']};
    color: {t['white']};
}}

QLineEdit, QSpinBox, QComboBox {{
    background-color: {t['surface2']}; border: 1px solid {t['border']}; border-radius: {rs}px;
    padding: 6px 10px; min-height: 20px; color: {t['text']};
}}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus {{ border-color: {t['accent']}; }}
QComboBox QAbstractItemView {{ background-color: {t['surface2']}; border: 1px solid {t['border']}; selection-background-color: {t['accent']}; }}

QLabel[sectionTitle="true"] {{ color: {t['text']}; font-size: 15px; font-weight: 700; }}
QLabel[statValue="true"] {{ color: {t['text']}; font-size: {t['font_metric']}px; font-weight: 700; }}
QLabel[statName="true"] {{ color: {t['muted']}; font-size: {t['font_small']}px; }}

QProgressBar {{ background-color: {t['window']}; border: none; border-radius: 4px; }}
QProgressBar::chunk {{ background-color: {t['success']}; border-radius: 4px; }}

QTabBar {{ background: transparent; }}
QTabBar::tab {{
    background: transparent; color: {t['muted']};
    padding: 8px 18px; border: none; border-bottom: 2px solid transparent;
    margin-right: 4px; font-size: {t['font_body']}px;
}}
QTabBar::tab:hover {{ color: {t['text']}; }}
QTabBar::tab:selected {{ color: {t['white']}; border-bottom: 2px solid {t['accent']}; font-weight: 600; }}

QTextEdit, QPlainTextEdit {{
    background-color: {t['window']}; border: 1px solid {t['border']}; border-radius: {rs}px;
    padding: 8px; color: {t['muted']}; font-size: {t['font_small']}px;
}}
QSplitter::handle {{ background-color: {t['border']}; }}
QSplitter::handle:horizontal {{ width: 1px; }}
"""


DARK_QSS = _qss()


def apply_theme(app) -> None:
    app.setStyle("Fusion")
    apply_dark_palette(app)
    app.setStyleSheet(DARK_QSS)
