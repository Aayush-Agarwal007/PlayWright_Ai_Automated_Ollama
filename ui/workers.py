"""Background worker so Ollama / Playwright never block the GUI thread."""
from __future__ import annotations

import logging
import threading
from typing import Any, Callable

from PySide6.QtCore import QThread, Signal

from services.models import OperationCancelled

log = logging.getLogger(__name__)


class TaskWorker(QThread):
    progress = Signal(str, int)
    item = Signal(object)
    succeeded = Signal(object)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, fn: Callable[["TaskWorker"], Any], parent=None) -> None:
        super().__init__(parent)
        self._fn = fn
        self.cancel_event = threading.Event()

    def report(self, message: str, percent: int = -1) -> None:
        self.progress.emit(message, percent)

    def request_cancel(self) -> None:
        self.cancel_event.set()

    def run(self) -> None:
        try:
            result = self._fn(self)
        except OperationCancelled:
            self.cancelled.emit()
        except Exception as exc:  # surfaced in the UI; never crashes the app
            log.exception("Background task failed")
            self.failed.emit(str(exc) or exc.__class__.__name__)
        else:
            self.succeeded.emit(result)
