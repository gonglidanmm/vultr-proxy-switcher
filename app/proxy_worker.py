"""把本地转发代理跑进 QThread（asyncio 不能直接跑在主线程）。"""

from __future__ import annotations

import asyncio

from PySide6.QtCore import QThread, Signal

from .localproxy import LocalSocks5Proxy


class ProxyWorker(QThread):
    status = Signal(str)  # "running" / "stopped" / "error: ..."

    def __init__(self, proxy: LocalSocks5Proxy, parent=None):
        super().__init__(parent)
        self._proxy = proxy

    def run(self) -> None:
        try:
            self.status.emit("running")
            asyncio.run(self._proxy.serve())
        except Exception as e:
            self.status.emit(f"error: {e}")
        finally:
            self.status.emit("stopped")

    def request_stop(self) -> None:
        self._proxy.stop()
