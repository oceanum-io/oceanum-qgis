# Copyright 2026 Oceanum / Dave Johnson
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""QGIS plugin class: wires Datamesh connections into the Browser and menus."""

from __future__ import annotations

import logging
from pathlib import Path

from qgis.core import QgsApplication
from qgis.PyQt.QtWidgets import QAction

from . import browser
from .icons import plugin_icon
from .workspace import ConnectionStore

PLUGIN_NAME = "Oceanum Datamesh"


class OceanumDatameshPlugin:
    """Entry point wired up by ``classFactory`` in ``__init__.py``."""

    def __init__(self, iface):
        self.iface = iface
        self.actions: list[QAction] = []
        self.toolbar_action: QAction | None = None
        self.browser_provider = None
        self.store: ConnectionStore | None = None
        self._menu_hooked = False

    # -- QGIS lifecycle ---------------------------------------------------- #
    def initGui(self) -> None:  # noqa: N802 (QGIS API)
        self.store = ConnectionStore(_connections_path())

        # Toolbar + Web menu: create a new connection.
        self.toolbar_action = QAction(
            plugin_icon(), "New Datamesh connection…", self.iface.mainWindow()
        )
        self.toolbar_action.setToolTip("Create a new Oceanum Datamesh connection")
        self.toolbar_action.triggered.connect(self.new_connection)
        self.iface.addToolBarIcon(self.toolbar_action)
        self.iface.addPluginToWebMenu(PLUGIN_NAME, self.toolbar_action)

        settings_action = QAction("Datamesh settings…", self.iface.mainWindow())
        settings_action.setToolTip("Set the Datamesh token, service and user")
        settings_action.triggered.connect(self.open_settings)
        self.iface.addPluginToWebMenu(PLUGIN_NAME, settings_action)

        self.actions = [self.toolbar_action, settings_action]

        # Add "Oceanum Datamesh" connections to the Browser (top-left Sources).
        self.browser_provider = browser.register(self.iface, self.store)

        # Group context menu: restyle all rasters in a group at once (QGIS 4
        # has no multi-selection Paste Style).
        view = self.iface.layerTreeView() if hasattr(self.iface, "layerTreeView") else None
        if view is not None and hasattr(view, "contextMenuAboutToShow"):
            view.contextMenuAboutToShow.connect(self._extend_layer_tree_menu)
            self._menu_hooked = True

    def _extend_layer_tree_menu(self, menu) -> None:
        from qgis.core import QgsLayerTree, QgsRasterLayer

        view = self.iface.layerTreeView()
        node = view.currentNode() if view is not None else None
        if node is None or not QgsLayerTree.isGroup(node):
            return
        if not any(
            isinstance(tree_layer.layer(), QgsRasterLayer) for tree_layer in node.findLayers()
        ):
            return
        action = menu.addAction("Set colour ramp for rasters…")
        action.triggered.connect(lambda: self._set_group_ramp(node))

    def _set_group_ramp(self, group) -> None:
        from qgis.core import Qgis, QgsStyle
        from qgis.PyQt.QtWidgets import QInputDialog

        from .layers import set_group_ramp
        from .tasks import push_message

        names = QgsStyle.defaultStyle().colorRampNames()
        name, ok = QInputDialog.getItem(
            self.iface.mainWindow(),
            "Colour ramp",
            "Apply to every raster in the group (value ranges are kept):",
            names,
            0,
            False,
        )
        if not ok or not name:
            return
        count = set_group_ramp(group, name)
        push_message(
            self.iface,
            f"Applied '{name}' to {count} raster layer(s).",
            Qgis.MessageLevel.Success if count else Qgis.MessageLevel.Warning,
        )

    def unload(self) -> None:
        if self._menu_hooked:
            try:
                self.iface.layerTreeView().contextMenuAboutToShow.disconnect(
                    self._extend_layer_tree_menu
                )
            except Exception:  # noqa: BLE001 - view may already be gone at shutdown
                logging.getLogger(__name__).debug("Menu unhook failed", exc_info=True)
            self._menu_hooked = False
        if self.browser_provider is not None:
            browser.unregister(self.browser_provider)
            self.browser_provider = None
        for action in self.actions:
            self.iface.removePluginWebMenu(PLUGIN_NAME, action)
        if self.toolbar_action is not None:
            self.iface.removeToolBarIcon(self.toolbar_action)
        self.actions = []
        self.toolbar_action = None

    # -- callbacks --------------------------------------------------------- #
    def new_connection(self, _checked: bool = False) -> None:
        browser.new_connection(self.iface.mainWindow())

    def open_settings(self, _checked: bool = False) -> None:
        browser.open_settings(self.iface.mainWindow())


def _connections_path() -> str:
    """Per-profile workspace file that stores the Datamesh connections."""
    profile = QgsApplication.qgisSettingsDirPath()
    return str(Path(profile) / "oceanum_datamesh" / "connections.json")
