from __future__ import annotations

import threading
import traceback
from typing import Callable

from PySide6.QtCore import QObject, QThread, Signal

from ..applog import get_logger

log = get_logger("task")


class TaskContext:
    def __init__(self, task: "Task") -> None:
        self._task = task

    def progress(self, percent: int | None, message: str) -> None:
        self._task.signals.progress.emit(percent, message)

    def log(self, message: str) -> None:
        self._task.signals.log.emit(message)

    def cancelled(self) -> bool:
        return self._task.cancel_event.is_set()

    @property
    def cancel_event(self) -> threading.Event:
        return self._task.cancel_event


class TaskSignals(QObject):
    progress = Signal(object, str)
    log = Signal(str)
    finished = Signal(object)
    failed = Signal(object, str, str)


class Task(QThread):
    def __init__(self, name: str, fn: Callable[[TaskContext], object], parent=None) -> None:
        super().__init__(parent)
        self.name = name
        self.fn = fn
        self.cancel_event = threading.Event()
        self.signals = TaskSignals()

    def run(self) -> None:
        context = TaskContext(self)
        try:
            result = self.fn(context)
        except Exception as exc:  # noqa: BLE001 - delivered to the interface
            message = str(exc) or exc.__class__.__name__
            hint = getattr(exc, "hint", "")
            if not isinstance(exc, (KeyboardInterrupt, SystemExit)):
                log.error("Task '%s' failed: %s\n%s", self.name, exc, traceback.format_exc())
            try:
                self.signals.failed.emit(exc, message, hint)
            except RuntimeError:
                pass
            return
        try:
            self.signals.finished.emit(result)
        except RuntimeError:
            pass

    def cancel(self) -> None:
        self.cancel_event.set()


def cancelled_exceptions() -> tuple[type[BaseException], ...]:
    from ..installer import InstallCancelled
    from ..site import DownloadCancelled

    return (InstallCancelled, DownloadCancelled)
