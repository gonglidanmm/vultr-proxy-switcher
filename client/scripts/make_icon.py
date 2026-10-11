"""生成客户端图标源 PNG（纯标准库，无 Pillow 依赖）。

画一个蓝色圆角方块 + 白色卫星轨道环 + 中心圆点。
输出 client/icons/source.png (1024x1024)。
"""

import struct
import zlib
from pathlib import Path

SIZE = 1024
BLUE = (10, 132, 255)       # #0a84ff
WHITE = (255, 255, 255)


def make_png(path: str) -> None:
    # 像素数据：每行前加一个 filter 字节 0
    raw = bytearray()
    for y in range(SIZE):
        raw.append(0)  # filter type 0 (None)
        for x in range(SIZE):
            r, g, b = pixel(x, y)
            raw += bytes((r, g, b))

    def chunk(tag: bytes, data: bytes) -> bytes:
        c = struct.pack(">I", len(data)) + tag + data
        c += struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        return c

    ihdr = struct.pack(">IIBBBBB", SIZE, SIZE, 8, 2, 0, 0, 0)  # 8bit RGB
    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", ihdr)
    png += chunk(b"IDAT", zlib.compress(bytes(raw), 9))
    png += chunk(b"IEND", b"")

    Path(path).write_bytes(png)


def pixel(x: int, y: int) -> tuple:
    cx, cy = SIZE / 2, SIZE / 2
    # 圆角方块背景（圆角半径 ~220）
    radius = 220
    in_rect = (radius <= x < SIZE - radius) or (radius <= y < SIZE - radius)
    if not in_rect:
        # 圆角判断
        corners = [
            (radius, radius), (SIZE - radius, radius),
            (radius, SIZE - radius), (SIZE - radius, SIZE - radius),
        ]
        in_rect = any((x - cx0) ** 2 + (y - cy0) ** 2 <= radius ** 2 for cx0, cy0 in corners)

    # 背景蓝色
    bg = BLUE

    # 卫星轨道环：中心圆环，外径 ~300，内径 ~250
    d = ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5
    ring_outer = 300
    ring_inner = 240
    # 椭圆环（横向拉伸模拟轨道透视）
    ey = (y - cy) * 1.6
    de = ((x - cx) ** 2 + ey ** 2) ** 0.5
    if ring_inner <= de <= ring_outer:
        return WHITE

    # 中心圆点
    if d <= 90:
        return WHITE

    return bg


if __name__ == "__main__":
    out = Path(__file__).resolve().parent.parent / "icons" / "source.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    make_png(str(out))
    print("已生成", out)
