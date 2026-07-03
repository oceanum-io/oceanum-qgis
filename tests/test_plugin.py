# Copyright 2026 Oceanum / Dave Johnson
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Smoke test: the plugin wires up and tears down cleanly (needs QGIS)."""

from __future__ import annotations

import pytest

pytest.importorskip("qgis.core", reason="QGIS Python bindings required")

from qgis.core import QgsApplication  # noqa: E402
from qgis.testing import start_app  # noqa: E402

start_app()

from qgis.testing.mocked import get_iface  # noqa: E402

import oceanum_datamesh.plugin as plugin_mod  # noqa: E402
from oceanum_datamesh.plugin import OceanumDatameshPlugin  # noqa: E402


def _provider_names():
    return [p.name() for p in QgsApplication.dataItemProviderRegistry().providers()]


def test_initgui_registers_and_unload_removes(tmp_path, monkeypatch):
    monkeypatch.setattr(plugin_mod, "_connections_path", lambda: str(tmp_path / "connections.json"))
    plugin = OceanumDatameshPlugin(get_iface())
    plugin.initGui()
    try:
        assert "Oceanum Datamesh" in _provider_names()
        assert plugin.store is not None
    finally:
        plugin.unload()
    assert "Oceanum Datamesh" not in _provider_names()
