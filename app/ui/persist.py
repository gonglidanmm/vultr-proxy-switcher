"""窗口大小 + 表格列宽持久化工具。

用法：
- 弹窗继承 SizedDialog，设置类属性 _size_key / _default_w / _default_h，
  打开自动恢复上次大小，关闭自动保存。
- 表格调 persist_col_widths(table, key, default_widths)，恢复列宽 + 拖列宽实时保存。
"""
from __future__ import annotations

from PySide6.QtWidgets import QDialog, QTableWidget

from .. import config


class SizedDialog(QDialog):
    """自动持久化窗口大小的弹窗基类。

    子类设置类属性：
    - _size_key: config 键名（空字符串 = 不持久化）
    - _default_w / _default_h: 默认宽高
    """

    _size_key: str = ""
    _default_w: int = 640
    _default_h: int = 480

    def __init__(self, parent=None):
        super().__init__(parent)
        self._restore_size()

    def _restore_size(self) -> None:
        if not self._size_key:
            return
        saved = config.get(self._size_key)
        if saved and len(saved) == 2:
            self.resize(int(saved[0]), int(saved[1]))
        else:
            self.resize(self._default_w, self._default_h)

    def closeEvent(self, event) -> None:
        if self._size_key:
            config.set_value(self._size_key, [self.width(), self.height()])
        super().closeEvent(event)


def persist_col_widths(table: QTableWidget, key: str, default_widths: list[int] | None = None) -> None:
    """给表格加列宽持久化：恢复保存的列宽，拖列宽实时保存。

    default_widths: 首次无保存时的默认列宽（列表，长度=列数）。
    """
    saved = config.get(key)
    n = table.columnCount()
    if saved and isinstance(saved, list) and len(saved) == n:
        for c in range(n):
            table.setColumnWidth(c, int(saved[c]))
    elif default_widths:
        for c, w in enumerate(default_widths):
            if c < n:
                table.setColumnWidth(c, w)
    hdr = table.horizontalHeader()
    hdr.sectionResized.connect(lambda *_: config.set_value(key, [table.columnWidth(c) for c in range(n)]))
