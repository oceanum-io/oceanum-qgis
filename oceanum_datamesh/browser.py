# Copyright 2026 Oceanum / Dave Johnson
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""QGIS Browser panel integration.

Adds an "Oceanum Datamesh" source to the Browser tree (top-left panel). Expand
it to browse the catalogue; double-click a dataset to open the plugin panel with
that dataset selected, ready to filter and load.
"""

from __future__ import annotations

import os

from qgis.core import (
    Qgis,
    QgsApplication,
    QgsDataCollectionItem,
    QgsDataItem,
    QgsDataItemProvider,
    QgsDataProvider,
    QgsSettings,
)
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction, QInputDialog

from .dependencies import oceanum_available
from .engine import DatameshEngine
from .gui.settings_dialog import SETTINGS_GROUP, load_connection_settings

ICON_PATH = os.path.join(os.path.dirname(__file__), "resources", "icon.svg")
PROVIDER_KEY = "oceanum_datamesh"
ROOT_PATH = "oceanum:/"

# The unfiltered catalogue is large, so list a fast sample; a search filter (set
# via the root's context menu) returns a bounded set and can list more.
DEFAULT_LIMIT = 100
FILTERED_LIMIT = 300
FILTER_KEY = f"{SETTINGS_GROUP}/browser_filter"


def _get_filter() -> str:
    return QgsSettings().value(FILTER_KEY, "", type=str)


def _set_filter(value: str) -> None:
    QgsSettings().setValue(FILTER_KEY, value or "")


# Set by the plugin so Browser items can open the dock. Signature: (id) -> None.
_PANEL_OPENER = None


def set_panel_opener(callback) -> None:
    """Register the callback used when a dataset item is double-clicked."""
    global _PANEL_OPENER
    _PANEL_OPENER = callback


def _open_in_panel(datasource_id: str) -> bool:
    if _PANEL_OPENER is None:
        return False
    _PANEL_OPENER(datasource_id)
    return True


def _engine() -> DatameshEngine:
    settings = load_connection_settings()
    return DatameshEngine(
        token=settings["token"] or None,
        service=settings["service"] or None,
        user=settings["user"] or None,
    )


def _is_gridded(coordinates: dict) -> bool:
    """Heuristic: a dataset with x/y but no station/geometry keys is a grid."""
    keys = set(coordinates or {})
    station = {"s", "i", "j", "g"}
    if station & keys:
        return False
    return {"x", "y"} <= keys


class DatameshDatasetItem(QgsDataItem):
    """A leaf item for one Datamesh datasource."""

    def __init__(self, parent, entry: dict):
        name = entry.get("name") or entry.get("id")
        super().__init__(
            Qgis.BrowserItemType.Custom,
            parent,
            name,
            f"{ROOT_PATH}{entry.get('id')}",
            PROVIDER_KEY,
        )
        self._datasource_id = entry.get("id")
        theme = "mIconRaster.svg" if _is_gridded(entry.get("coordinates")) else "mIconVector.svg"
        self.setIcon(QgsApplication.getThemeIcon(theme))
        tip = entry.get("id")
        if entry.get("tstart"):
            tip += f"\n{entry.get('tstart')} → {entry.get('tend') or 'now'}"
        self.setToolTip(tip)

    def hasChildren(self) -> bool:
        return False

    def handleDoubleClick(self) -> bool:
        return _open_in_panel(self._datasource_id)


class DatameshMessageItem(QgsDataItem):
    """A non-actionable informational leaf (e.g. 'configure your token')."""

    def __init__(self, parent, message: str):
        super().__init__(
            Qgis.BrowserItemType.Custom, parent, message, f"{ROOT_PATH}__msg__", PROVIDER_KEY
        )
        self.setIcon(QgsApplication.getThemeIcon("mIconWarning.svg"))

    def hasChildren(self) -> bool:
        return False


class DatameshRootItem(QgsDataCollectionItem):
    """The 'Oceanum Datamesh' root shown in the Browser."""

    def __init__(self, parent):
        super().__init__(parent, "Oceanum Datamesh", ROOT_PATH, PROVIDER_KEY)
        self.setIcon(QIcon(ICON_PATH))

    def createChildren(self) -> list:
        if not oceanum_available():
            return [DatameshMessageItem(self, "Install the 'oceanum' package (see the panel)")]
        engine = _engine()
        if not engine.has_token:
            return [DatameshMessageItem(self, "Set a Datamesh token in the panel settings")]

        text = _get_filter()
        limit = FILTERED_LIMIT if text else DEFAULT_LIMIT
        try:
            results = engine.search(text=text or None, limit=limit)
        except Exception as exc:  # noqa: BLE001
            return [DatameshMessageItem(self, f"Datamesh error: {exc}")]

        results.sort(key=lambda e: (e.get("name") or e.get("id") or "").lower())
        children: list = [DatameshDatasetItem(self, entry) for entry in results]
        if not children:
            hint = f"No datasets match '{text}'" if text else "No datasets available"
            children.append(DatameshMessageItem(self, hint))
        elif len(results) >= limit:
            note = (
                "Showing first results — refine the filter"
                if text
                else "Showing a sample — right-click to set a catalog filter"
            )
            children.append(DatameshMessageItem(self, note))
        return children

    def actions(self, parent):  # noqa: N802 (QGIS API)
        current = _get_filter()
        set_filter = QAction(
            QgsApplication.getThemeIcon("mActionFilter2.svg"),
            "Set catalog filter…",
            parent,
        )
        set_filter.triggered.connect(lambda: self._prompt_filter(parent))
        actions = [set_filter]
        if current:
            clear = QAction("Clear filter", parent)
            clear.triggered.connect(self._clear_filter)
            actions.append(clear)
        return actions

    def _prompt_filter(self, parent) -> None:
        text, ok = QInputDialog.getText(
            parent,
            "Datamesh catalog filter",
            "Show datasets matching (blank = sample of all):",
            text=_get_filter(),
        )
        if ok:
            _set_filter(text.strip())
            self.refresh()

    def _clear_filter(self) -> None:
        _set_filter("")
        self.refresh()


class DatameshDataItemProvider(QgsDataItemProvider):
    """Registers the Datamesh root with the Browser."""

    def name(self) -> str:
        return "Oceanum Datamesh"

    def dataProviderKey(self) -> str:
        return PROVIDER_KEY

    def capabilities(self) -> int:
        return int(QgsDataProvider.DataCapability.Net)

    def createDataItem(self, path: str, parentItem):  # noqa: N803 (QGIS API)
        if not path:  # root of the Browser tree
            return DatameshRootItem(parentItem)
        return None


def register(panel_opener) -> DatameshDataItemProvider:
    """Register the Browser provider and return it (for later removal)."""
    set_panel_opener(panel_opener)
    provider = DatameshDataItemProvider()
    QgsApplication.dataItemProviderRegistry().addProvider(provider)
    return provider


def unregister(provider) -> None:
    if provider is not None:
        QgsApplication.dataItemProviderRegistry().removeProvider(provider)
    set_panel_opener(None)
