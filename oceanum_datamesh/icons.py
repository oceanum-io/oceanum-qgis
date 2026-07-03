# Copyright 2026 Oceanum / Dave Johnson
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Theme-aware selection of the plugin's brand icon.

QGIS auto-themes its own icons per UI theme, but a plugin that hands QGIS a
fixed file gets that same file on every theme. The Oceanum mark is dark marine,
so on a dark UI theme (e.g. "Night Mapping") it would vanish against the chrome.
We ship a pale variant and choose between them from the active palette, which
also covers "Blend of Gray" and any custom theme.
"""

from __future__ import annotations

import os

from qgis.core import QgsApplication
from qgis.PyQt.QtGui import QIcon, QPalette

_RES_DIR = os.path.join(os.path.dirname(__file__), "resources")
_DARK_MARK = os.path.join(_RES_DIR, "icon.svg")  # marine mark — for light themes
_LIGHT_MARK = os.path.join(_RES_DIR, "icon-light.svg")  # pale mark — for dark themes


def _dark_ui() -> bool:
    """True when the active UI theme has a dark window background."""
    window = QgsApplication.palette().color(QPalette.ColorRole.Window)
    return window.lightness() < 128


def plugin_icon_path() -> str:
    """Path to the brand icon variant that suits the current UI theme."""
    return _LIGHT_MARK if _dark_ui() else _DARK_MARK


def plugin_icon() -> QIcon:
    """The plugin's brand icon, matched to the current UI theme."""
    return QIcon(plugin_icon_path())
