"""支持行拖拽排序的表格（自研，避免 Qt InternalMove 在 macOS 上复制/错位）。"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QTableWidget


class DragTableWidget(QTableWidget):
    """拖拽排序表格。

    dropEvent 只计算「从哪行拖到哪行」，发出 row_dropped(src_row, dst_row) 信号，
    实际顺序重排由外部回调完成（重排数据 + 重新 populate）。
    表格行始终由 populate 统一渲染，因此绝不复制、绝不错位。
    """

    row_dropped = Signal(int, int)  # (src_row, dst_row)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setDragDropMode(QTableWidget.DragDropMode.DragDrop)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)

    def mimeData(self, items):
        md = super().mimeData(items)
        if items:
            md.setData("application/x-table-row", str(items[0].row()).encode())
        return md

    def dragEnterEvent(self, event):
        if event.source() is self:
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if event.source() is self:
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        if event.source() is not self:
            event.ignore()
            return
        src_row = -1
        try:
            data = event.mimeData().data("application/x-table-row")
            if data:
                src_row = int(bytes(data).decode())
        except Exception:
            src_row = -1
        if src_row < 0 or src_row >= self.rowCount():
            event.ignore()
            return
        pos = event.position().toPoint()
        target = self.indexAt(pos)
        # 拖到某行上 = 放到那一行的位置；拖到空白 = 放到最后
        dst_row = target.row() if target.isValid() else (self.rowCount() - 1)
        event.accept()
        self.row_dropped.emit(src_row, dst_row)
