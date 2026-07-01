# Copyright 2026 Oceanum / Dave Johnson
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""QGIS plugin class: wires the dock widget into the QGIS GUI."""

from __future__ import annotations

import os

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction, QDockWidget

from . import browser
from .gui.dock import DatameshPanel

PLUGIN_NAME = "Oceanum Datamesh"
ICON_PATH = os.path.join(os.path.dirname(__file__), "resources", "icon.svg")


class OceanumDatameshPlugin:
    """Entry point wired up by ``classFactory`` in ``__init__.py``."""

    def __init__(self, iface):
        self.iface = iface
        self.action: QAction | None = None
        self.dock: QDockWidget | None = None
        self.browser_provider = None

    # -- QGIS lifecycle ---------------------------------------------------- #
    def initGui(self) -> None:  # noqa: N802 (QGIS API)
        icon = QIcon(ICON_PATH)
        self.action = QAction(icon, PLUGIN_NAME, self.iface.mainWindow())
        self.action.setCheckable(True)
        self.action.setToolTip("Search and load Oceanum Datamesh data")
        self.action.triggered.connect(self.toggle_dock)

        self.iface.addPluginToWebMenu(PLUGIN_NAME, self.action)
        self.iface.addToolBarIcon(self.action)

        self.dock = QDockWidget(PLUGIN_NAME, self.iface.mainWindow())
        self.dock.setObjectName("OceanumDatameshDock")
        self.dock.setWidget(DatameshPanel(self.iface, self.dock))
        self.dock.visibilityChanged.connect(self._on_visibility_changed)
        self.iface.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.dock)
        self.dock.hide()

        # Add "Oceanum Datamesh" to the Browser panel (top-left sources tree).
        self.browser_provider = browser.register(self.open_datasource_in_panel)

    def unload(self) -> None:
        if self.browser_provider is not None:
            browser.unregister(self.browser_provider)
            self.browser_provider = None
        if self.action is not None:
            self.iface.removePluginWebMenu(PLUGIN_NAME, self.action)
            self.iface.removeToolBarIcon(self.action)
            self.action = None
        if self.dock is not None:
            self.iface.removeDockWidget(self.dock)
            self.dock.deleteLater()
            self.dock = None

    # -- callbacks --------------------------------------------------------- #
    def toggle_dock(self, checked: bool) -> None:
        if self.dock is not None:
            self.dock.setVisible(checked)

    def _on_visibility_changed(self, visible: bool) -> None:
        if self.action is not None:
            self.action.setChecked(visible)

    def open_datasource_in_panel(self, datasource_id: str) -> None:
        """Show the dock and load a datasource into it (from a Browser item)."""
        if self.dock is None:
            return
        self.dock.setVisible(True)
        self.dock.raise_()
        self.dock.widget().show_datasource(datasource_id)
