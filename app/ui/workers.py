"""通用后台 worker：把阻塞/网络调用丢到 QThread，避免卡 UI。"""

from __future__ import annotations

from PySide6.QtCore import QThread, Signal


class CallWorker(QThread):
    """跑一个无参 callable，成功 emit `done(结果)`，失败 emit `failed(错误信息)`。"""

    done = Signal(object)
    failed = Signal(str)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self._fn = fn

    def run(self) -> None:
        try:
            self.done.emit(self._fn())
        except Exception as e:  # noqa: BLE001
            self.failed.emit(str(e))


class ProgressWorker(QThread):
    """带进度回显的 worker：fn(progress=callable) 里调用 progress(msg) 实时上报。"""

    done = Signal(object)
    failed = Signal(str)
    progress = Signal(str)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self._fn = fn

    def run(self) -> None:
        try:
            self.done.emit(self._fn(progress=self.progress.emit))
        except Exception as e:  # noqa: BLE001
            self.failed.emit(str(e))
