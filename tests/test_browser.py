# Copyright 2026 Oceanum / Dave Johnson
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Tests for the Browser connections provider (needs QGIS bindings)."""

from __future__ import annotations

import pytest

pytest.importorskip("qgis.core", reason="QGIS Python bindings required")

from qgis.testing import start_app  # noqa: E402

start_app()

from oceanum.datamesh import Query  # noqa: E402
from qgis.testing.mocked import get_iface  # noqa: E402

from oceanum_datamesh import browser  # noqa: E402
from oceanum_datamesh.workspace import ConnectionStore  # noqa: E402


def _store_with(tmp_path, *labels):
    store = ConnectionStore(tmp_path / "connections.json")
    for label in labels:
        store.add(Query(datasource="oceanum_wave_glob_era5"), label=label)
    return store


def test_root_lists_saved_connections(tmp_path, monkeypatch):
    monkeypatch.setenv("DATAMESH_TOKEN", "token")
    store = _store_with(tmp_path, "Waves", "Wind")
    provider = browser.register(get_iface(), store)
    try:
        root = provider.createDataItem("", None)
        items = [c for c in root.createChildren() if isinstance(c, browser.DatameshConnectionItem)]
        assert [i.name() for i in items] == ["Waves", "Wind"]
    finally:
        browser.unregister(provider)


def test_root_without_connections_shows_hint(tmp_path, monkeypatch):
    monkeypatch.setenv("DATAMESH_TOKEN", "token")
    provider = browser.register(get_iface(), _store_with(tmp_path))
    try:
        children = provider.createDataItem("", None).createChildren()
        assert len(children) == 1
        assert isinstance(children[0], browser.DatameshMessageItem)
    finally:
        browser.unregister(provider)


def test_root_without_token_prompts_for_settings(tmp_path, monkeypatch):
    monkeypatch.delenv("DATAMESH_TOKEN", raising=False)
    provider = browser.register(get_iface(), _store_with(tmp_path, "Waves"))
    try:
        children = provider.createDataItem("", None).createChildren()
        assert isinstance(children[0], browser.DatameshMessageItem)
    finally:
        browser.unregister(provider)


def test_capabilities_returns_flag_not_int(tmp_path):
    # QGIS 4 rejects a plain int here; it must be the QFlags/enum member.
    caps = browser.DatameshDataItemProvider().capabilities()
    assert type(caps) is not int  # noqa: E721 (exact-type check is the point)
    assert int(caps) != 0
