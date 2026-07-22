# Copyright 2026 Oceanum / Dave Johnson
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""A generic :class:`QgsTask` that runs a callable off the GUI thread.

Network access and file writing happen in :meth:`FunctionTask.run` (background
thread); the ``on_complete`` callback fires from :meth:`FunctionTask.finished`
(main thread), which is where map layers may safely be created.
"""

from __future__ import annotations

from typing import Callable

from qgis.core import Qgis, QgsApplication, QgsMessageLog, QgsTask

LOG_TAG = "Oceanum Datamesh"


def log(message: str, level=Qgis.MessageLevel.Info) -> None:
    QgsMessageLog.logMessage(str(message), LOG_TAG, level)


def push_message(iface, text: str, level=Qgis.MessageLevel.Info) -> None:
    """Show a message in the QGIS message bar under the plugin's tag."""
    if iface is not None:
        iface.messageBar().pushMessage(LOG_TAG, text, level=level)


def run_task(description: str, work: Callable, done: Callable, registry: list) -> FunctionTask:
    """Submit a :class:`FunctionTask` to the QGIS task manager.

    The task is kept alive in *registry* (a caller-owned list) until it finishes,
    then removed — QGIS drops tasks whose only reference is the manager, so the
    registry prevents premature garbage collection. Returns the task.
    """
    task = FunctionTask(description, work, done)
    registry.append(task)

    def _cleanup(*_):
        if task in registry:
            registry.remove(task)

    task.taskCompleted.connect(_cleanup)
    task.taskTerminated.connect(_cleanup)
    QgsApplication.taskManager().addTask(task)
    return task


class FunctionTask(QgsTask):
    """Run ``run_fn(task)`` in the background and report back on the main thread.

    ``run_fn`` receives this task so it can call :meth:`setProgress` or check
    :meth:`isCanceled`. Its return value is delivered to ``on_complete`` as
    ``on_complete(ok: bool, result, error: Exception | None)``.
    """

    def __init__(
        self,
        description: str,
        run_fn: Callable,
        on_complete: Callable,
    ):
        super().__init__(description, QgsTask.Flag.CanCancel)
        self._run_fn = run_fn
        self._on_complete = on_complete
        self.result = None
        self.error: Exception | None = None

    def run(self) -> bool:  # background thread
        try:
            self.result = self._run_fn(self)
            return True
        except Exception as exc:  # noqa: BLE001 - reported to the GUI
            self.error = exc
            log(f"{self.description()} failed: {exc}", Qgis.MessageLevel.Warning)
            return False

    def finished(self, ok: bool) -> None:  # main thread
        if self._on_complete is not None:
            self._on_complete(bool(ok) and self.error is None, self.result, self.error)
