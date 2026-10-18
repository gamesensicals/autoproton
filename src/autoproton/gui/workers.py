"""Run slow work (network, disk, waiting for Steam) off the UI thread.

Results come back through Qt signals owned by an object that lives on the UI
thread, so callbacks always run on the UI thread and can touch widgets.
"""

from __future__ import annotations

import threading
import traceback
from collections.abc import Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot

from ..log import log


def friendly(exc: BaseException) -> str:
    """A message for the user. Our own errors already carry one."""
    text = str(exc).strip()
    if type(exc).__module__.startswith("autoproton") and text:
        return text
    return f"Something went wrong: {type(exc).__name__}: {text}" if text else f"Something went wrong ({type(exc).__name__})."


class _Relay(QObject):
    done = Signal(object)
    failed = Signal(object)
    progress = Signal(object)

    def __init__(self, on_done, on_failed, on_progress, finished):
        super().__init__()
        self._on_done, self._on_failed, self._on_progress, self._finished = on_done, on_failed, on_progress, finished
        self.done.connect(self._handle_done)
        self.failed.connect(self._handle_failed)
        self.progress.connect(self._handle_progress)

    @Slot(object)
    def _handle_done(self, result):
        try:
            if self._on_done:
                self._on_done(result)
        finally:
            self._finished(self)

    @Slot(object)
    def _handle_failed(self, exc):
        try:
            if self._on_failed:
                self._on_failed(exc)
        finally:
            self._finished(self)

    @Slot(object)
    def _handle_progress(self, value):
        if self._on_progress:
            self._on_progress(value)


class Task(QRunnable):
    def __init__(self, fn: Callable, relay: _Relay, pass_task: bool):
        super().__init__()
        self.fn = fn
        self.relay = relay
        self.pass_task = pass_task
        self.cancel = threading.Event()
        self.setAutoDelete(False)

    def report(self, value) -> None:
        self.relay.progress.emit(value)

    def run(self):
        try:
            result = self.fn(self) if self.pass_task else self.fn()
        except BaseException as e:  # noqa: BLE001 - reported to the UI
            log.warning("background task failed: %s", "".join(traceback.format_exception(e)))
            self.relay.failed.emit(e)
        else:
            self.relay.done.emit(result)


class Runner:
    """Starts tasks on a thread pool. ``fn`` gets the Task when ``with_task`` is set (for progress/cancel)."""

    def __init__(self, max_threads: int = 4):
        self.pool = QThreadPool()
        self.pool.setMaxThreadCount(max_threads)
        self._live: set = set()

    def run(self, fn: Callable, on_done=None, on_failed=None, on_progress=None, with_task: bool = False) -> Task:
        relay = _Relay(on_done, on_failed, on_progress, self._forget)
        task = Task(fn, relay, with_task)
        self._live.add((relay, task))
        relay._task = task
        self.pool.start(task)
        return task

    def _forget(self, relay):
        self._live = {pair for pair in self._live if pair[0] is not relay}

    def wait(self, msecs: int = 30000) -> bool:
        return self.pool.waitForDone(msecs)


class SyncRunner(Runner):
    """Runs tasks immediately on the calling thread. For tests."""

    def __init__(self):
        self._live = set()

    def run(self, fn, on_done=None, on_failed=None, on_progress=None, with_task=False):
        relay = _Relay(on_done, on_failed, on_progress, self._forget)
        task = Task(fn, relay, with_task)
        task.run()
        return task

    def wait(self, msecs: int = 0) -> bool:
        return True
