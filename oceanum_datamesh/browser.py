# Copyright 2026 Oceanum / Dave Johnson
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""QGIS Browser integration: Datamesh connections in the Sources tree.

Modelled on ArcGIS-style managed connections. The "Oceanum Datamesh" root holds
named *connections* — each a saved Datamesh query (a view of a datasource),
persisted in the Datamesh Workspace schema. Right-click the root to create one
(via the connection dialog, which stages and checks map-compatibility); expand
it to see saved connections; double-click a connection to load its view.
"""

from __future__ import annotations

from qgis.core import (
    Qgis,
    QgsApplication,
    QgsDataCollectionItem,
    QgsDataItem,
    QgsDataItemProvider,
    QgsDataProvider,
)
from qgis.PyQt.QtWidgets import QAction, QMessageBox

from .dependencies import oceanum_available
from .engine import DatameshEngine
from .gui.settings_dialog import SettingsDialog, load_connection_settings
from .icons import plugin_icon
from .workspace import connection_label

PROVIDER_KEY = "oceanum_datamesh"
ROOT_PATH = "oceanum:/"


# --------------------------------------------------------------------------- #
# Context (set by the plugin) + background task tracking
# --------------------------------------------------------------------------- #
class _Context:
    iface = None
    store = None  # a workspace.ConnectionStore


_CONTEXT = _Context()
_ROOT_ITEM = None
_TASKS: list = []


def set_context(iface, store) -> None:
    _CONTEXT.iface = iface
    _CONTEXT.store = store


def _main_window():
    return _CONTEXT.iface.mainWindow() if _CONTEXT.iface is not None else None


def _engine() -> DatameshEngine:
    settings = load_connection_settings()
    return DatameshEngine(
        token=settings["token"] or None,
        service=settings["service"] or None,
        user=settings["user"] or None,
    )


def _message(text: str, level=Qgis.Info) -> None:
    if _CONTEXT.iface is not None:
        _CONTEXT.iface.messageBar().pushMessage("Oceanum Datamesh", text, level=level)


def _refresh_root() -> None:
    if _ROOT_ITEM is not None:
        _ROOT_ITEM.refresh()


def _run_task(description, work, done) -> None:
    from .tasks import FunctionTask

    task = FunctionTask(description, work, done)
    _TASKS.append(task)

    def cleanup(*_):
        if task in _TASKS:
            _TASKS.remove(task)

    task.taskCompleted.connect(cleanup)
    task.taskTerminated.connect(cleanup)
    QgsApplication.taskManager().addTask(task)


# --------------------------------------------------------------------------- #
# Connection actions
# --------------------------------------------------------------------------- #
def open_settings(parent=None) -> bool:
    dialog = SettingsDialog(parent or _main_window())
    accepted = bool(dialog.exec())
    if accepted:
        _refresh_root()
    return accepted


def _ensure_ready(engine, parent) -> bool:
    if not oceanum_available():
        _message("Install the 'oceanum' Python package first.", Qgis.Warning)
        return False
    if not engine.has_token:
        _message("Set a Datamesh token to continue.", Qgis.Info)
        if open_settings(parent):
            return _engine().has_token
        return False
    return True


def new_connection(parent=None) -> None:
    if not _ensure_ready(_engine(), parent) or _CONTEXT.store is None:
        return
    from .gui.connection_dialog import ConnectionDialog

    dialog = ConnectionDialog(_CONTEXT.iface, _engine(), parent=parent or _main_window())
    if dialog.exec():
        label, query = dialog.result()
        if query is not None:
            _CONTEXT.store.add(query, label=label)
            _refresh_root()
            _message(f"Saved connection '{label}'.", Qgis.Success)


def edit_connection(connection, parent=None) -> None:
    if not _ensure_ready(_engine(), parent) or _CONTEXT.store is None:
        return
    from .gui.connection_dialog import ConnectionDialog

    dialog = ConnectionDialog(
        _CONTEXT.iface, _engine(), connection=connection, parent=parent or _main_window()
    )
    if dialog.exec():
        label, query = dialog.result()
        if query is not None:
            _CONTEXT.store.update(connection.id, query, label=label)
            _refresh_root()


def duplicate_connection(connection) -> None:
    if _CONTEXT.store is None:
        return
    query = connection.query
    query = query.model_copy() if hasattr(query, "model_copy") else query
    try:
        query.id = None  # force a fresh id on add
    except Exception:  # noqa: BLE001
        pass
    label = f"{connection_label(connection)} (copy)"
    _CONTEXT.store.add(query, label=label)
    _refresh_root()


def delete_connection(connection, parent=None) -> None:
    if _CONTEXT.store is None:
        return
    name = connection_label(connection)
    answer = QMessageBox.question(
        parent or _main_window(),
        "Delete connection",
        f"Delete the Datamesh connection '{name}'?",
    )
    if answer == QMessageBox.StandardButton.Yes:
        _CONTEXT.store.remove(connection.id)
        _refresh_root()


def load_connection(connection) -> None:
    """Run a saved connection's query and add the resulting layer(s) to the map."""
    engine = _engine()
    if not oceanum_available():
        _message("Install the 'oceanum' Python package first.", Qgis.Warning)
        return
    if not engine.has_token:
        _message("Set a Datamesh token (right-click → Datamesh settings…).", Qgis.Warning)
        return
    name = connection_label(connection)
    variables = list(getattr(connection.query, "variables", None) or [])
    _message(f"Loading '{name}' from Datamesh…", Qgis.Info)

    def work(_task):
        from . import layers

        return layers.query_to_layer_specs(
            engine, connection.query, name, coordinates={}, variables=variables or None
        )

    def done(ok, result, error):
        from . import layers

        if not ok:
            _message(f"{name}: {error}", Qgis.Warning)
            return
        added, failed = layers.add_layer_specs(result or [])
        if added:
            _message(f"Loaded {added} layer(s) from '{name}'.", Qgis.Success)
        for failed_name in failed:
            _message(f"Could not load layer: {failed_name}", Qgis.Warning)
        if not added and not failed:
            _message(f"'{name}' returned no layers.", Qgis.Warning)

    _run_task(f"Load {name}", work, done)


# --------------------------------------------------------------------------- #
# Browser items
# --------------------------------------------------------------------------- #
class DatameshMessageItem(QgsDataItem):
    """A non-actionable informational leaf (e.g. 'set your token')."""

    def __init__(self, parent, message: str):
        super().__init__(
            Qgis.BrowserItemType.Custom, parent, message, f"{ROOT_PATH}__msg__", PROVIDER_KEY
        )
        self.setIcon(QgsApplication.getThemeIcon("mIconInfo.svg"))

    def hasChildren(self) -> bool:
        return False


class DatameshConnectionItem(QgsDataItem):
    """A saved Datamesh connection (one workspace query view)."""

    def __init__(self, parent, connection):
        super().__init__(
            Qgis.BrowserItemType.Layer,
            parent,
            connection_label(connection) or connection.datasource,
            f"{ROOT_PATH}{connection.id}",
            PROVIDER_KEY,
        )
        self._connection = connection
        self.setIcon(QgsApplication.getThemeIcon("mIconLayer.svg"))
        self.setToolTip(_connection_tooltip(connection))

    def hasChildren(self) -> bool:
        return False

    def handleDoubleClick(self) -> bool:
        load_connection(self._connection)
        return True

    def actions(self, parent):  # noqa: N802 (QGIS API)
        add = QAction(QgsApplication.getThemeIcon("mActionAddLayer.svg"), "Add to map", parent)
        add.triggered.connect(lambda: load_connection(self._connection))
        edit = QAction("Edit…", parent)
        edit.triggered.connect(lambda: edit_connection(self._connection, parent))
        duplicate = QAction("Duplicate", parent)
        duplicate.triggered.connect(lambda: duplicate_connection(self._connection))
        delete = QAction("Delete", parent)
        delete.triggered.connect(lambda: delete_connection(self._connection, parent))
        return [add, edit, duplicate, delete]


class DatameshRootItem(QgsDataCollectionItem):
    """The 'Oceanum Datamesh' root shown in the Browser."""

    def __init__(self, parent):
        super().__init__(parent, "Oceanum Datamesh", ROOT_PATH, PROVIDER_KEY)
        self.setIcon(plugin_icon())

    def createChildren(self) -> list:
        if not oceanum_available():
            return [DatameshMessageItem(self, "Install the 'oceanum' package (Datamesh settings…)")]
        if _CONTEXT.store is None:
            return [DatameshMessageItem(self, "Datamesh is still initialising…")]
        if not _engine().has_token:
            return [DatameshMessageItem(self, "Set a token — right-click → Datamesh settings…")]
        connections = _CONTEXT.store.list()
        if not connections:
            return [DatameshMessageItem(self, "No connections yet — right-click → New Connection…")]
        return [DatameshConnectionItem(self, conn) for conn in connections]

    def actions(self, parent):  # noqa: N802 (QGIS API)
        new = QAction(QgsApplication.getThemeIcon("mActionAdd.svg"), "New Connection…", parent)
        new.triggered.connect(lambda: new_connection(parent))
        settings = QAction("Datamesh settings…", parent)
        settings.triggered.connect(lambda: open_settings(parent))
        refresh = QAction("Refresh", parent)
        refresh.triggered.connect(self.refresh)
        return [new, settings, refresh]


class DatameshDataItemProvider(QgsDataItemProvider):
    """Registers the Datamesh root with the Browser."""

    def name(self) -> str:
        return "Oceanum Datamesh"

    def dataProviderKey(self) -> str:
        return PROVIDER_KEY

    def capabilities(self):  # noqa: ANN201 (QGIS API — QFlags return)
        return QgsDataProvider.DataCapability.Net

    def createDataItem(self, path: str, parentItem):  # noqa: N803 (QGIS API)
        if not path:  # root of the Browser tree
            global _ROOT_ITEM
            _ROOT_ITEM = DatameshRootItem(parentItem)
            return _ROOT_ITEM
        return None


def _connection_tooltip(connection) -> str:
    query = connection.query
    lines = [connection.datasource]
    variables = getattr(query, "variables", None)
    if variables:
        lines.append("variables: " + ", ".join(list(variables)[:8]))
    if getattr(query, "timefilter", None) is not None:
        lines.append("time filter set")
    if getattr(query, "geofilter", None) is not None:
        lines.append("geo filter set")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Registration
# --------------------------------------------------------------------------- #
def register(iface, store) -> DatameshDataItemProvider:
    """Register the Browser provider and return it (for later removal)."""
    set_context(iface, store)
    provider = DatameshDataItemProvider()
    QgsApplication.dataItemProviderRegistry().addProvider(provider)
    return provider


def unregister(provider) -> None:
    global _ROOT_ITEM
    if provider is not None:
        QgsApplication.dataItemProviderRegistry().removeProvider(provider)
    set_context(None, None)
    _ROOT_ITEM = None
