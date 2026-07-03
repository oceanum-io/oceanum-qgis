# Copyright 2026 Oceanum / Dave Johnson
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Unit tests for theme-aware icon selection (needs QGIS bindings)."""

from __future__ import annotations

import os

import pytest

pytest.importorskip("qgis.core", reason="QGIS Python bindings required")

from qgis.core import QgsApplication  # noqa: E402
from qgis.PyQt.QtGui import QColor, QPalette  # noqa: E402
from qgis.testing import start_app  # noqa: E402

from oceanum_datamesh import icons  # noqa: E402

start_app()


@pytest.fixture
def restore_palette():
    """Set the app window colour for a test, then restore it afterwards."""
    original = QgsApplication.palette()

    def _apply(lightness_dark: bool) -> None:
        palette = QgsApplication.palette()
        colour = QColor(30, 30, 30) if lightness_dark else QColor(240, 240, 240)
        palette.setColor(QPalette.ColorRole.Window, colour)
        QgsApplication.setPalette(palette)

    yield _apply
    QgsApplication.setPalette(original)


def test_both_icon_variants_exist():
    assert os.path.exists(icons._DARK_MARK)
    assert os.path.exists(icons._LIGHT_MARK)


def test_light_theme_uses_dark_mark(restore_palette):
    restore_palette(lightness_dark=False)
    assert icons._dark_ui() is False
    assert icons.plugin_icon_path() == icons._DARK_MARK


def test_dark_theme_uses_light_mark(restore_palette):
    restore_palette(lightness_dark=True)
    assert icons._dark_ui() is True
    assert icons.plugin_icon_path() == icons._LIGHT_MARK
