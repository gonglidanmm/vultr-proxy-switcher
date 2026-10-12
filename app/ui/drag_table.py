"""支持行拖拽排序的表格（纯鼠标事件实现，完全不用 Qt 拖拽机制，杜绝复制/错位）。"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QTableWidget


class DragTableWidget(QTableWidget):
    """拖拽排序表格。

    完全不用 Qt 的 DragDrop/InternalMove（macOS 上会复制行、错位列），
    改为纯鼠标事件：按下记录起始行 → 移动超过阈值激活拖拽 → 释放时计算目标行，
    发出 row_dropped(src_row, dst_row) 信号，由外部重排数据 + 重新 populate。
    表格行始终由 populate 统一渲染，因此绝不复制、绝不错位。
    """

    row_dropped = Signal(int, int)  # (src_row, dst_row)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 完全禁用 Qt 拖拽
        self.setDragDropMode(QTableWidget.DragDropMode.NoDragDrop)
        self.setDragEnabled(False)
        self.setAcceptDrops(False)
        self._drag_src_row = -1
        self._drag_active = False
        self._press_pos = None

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            idx = self.indexAt(event.position().toPoint())
            if idx.isValid():
                self._drag_src_row = idx.row()
                self._press_pos = event.position()
                self._drag_active = False
            else:
                self._drag_src_row = -1
                self._press_pos = None
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        # 按住左键拖动超过阈值 → 激活拖拽模式
        if (
            self._drag_src_row >= 0
            and self._press_pos is not None
            and event.buttons() & Qt.MouseButton.LeftButton
        ):
            delta = event.position() - self._press_pos
            if delta.manhattanLength() > 12 and not self._drag_active:
                self._drag_active = True
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._drag_active and self._drag_src_row >= 0:
            target = self.indexAt(event.position().toPoint())
            if target.isValid() and target.row() != self._drag_src_row:
                self.row_dropped.emit(self._drag_src_row, target.row())
        self._drag_src_row = -1
        self._drag_active = False
        self._press_pos = None
        self.setCursor(Qt.CursorShape.ArrowCursor)
        super().mouseReleaseEvent(event)
