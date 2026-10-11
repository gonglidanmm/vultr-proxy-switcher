"""把 assets/icon.svg 渲染成多尺寸 PNG + macOS .icns（供 Dock / 窗口图标使用）。"""

from __future__ import annotations

import subprocess
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

ROOT = Path(__file__).resolve().parent.parent
SVG = ROOT / "assets" / "icon.svg"


def _render(size: int, out: Path) -> None:
    r = QSvgRenderer(str(SVG))
    img = QImage(size, size, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    r.render(p)
    p.end()
    img.save(str(out))


def main() -> None:
    for s in (256, 512):
        _render(s, ROOT / "assets" / f"icon_{s}.png")
        print("png", s)

    iconset = ROOT / "assets" / "icon.iconset"
    iconset.mkdir(exist_ok=True)
    mapping = {
        "icon_16x16.png": 16, "icon_16x16@2x.png": 32,
        "icon_32x32.png": 32, "icon_32x32@2x.png": 64,
        "icon_128x128.png": 128, "icon_128x128@2x.png": 256,
        "icon_256x256.png": 256, "icon_256x256@2x.png": 512,
        "icon_512x512.png": 512, "icon_512x512@2x.png": 1024,
    }
    for name, s in mapping.items():
        _render(s, iconset / name)

    subprocess.run(
        ["iconutil", "-c", "icns", str(iconset), "-o", str(ROOT / "assets" / "icon.icns")],
        check=True,
    )
    print("icns done")


if __name__ == "__main__":
    main()
