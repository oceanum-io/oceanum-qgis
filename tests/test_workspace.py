# Copyright 2026 Oceanum / Dave Johnson
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Offline tests for connection persistence in the Datamesh Workspace schema.

The workspace entity (https://schemas.oceanum.io/datamesh/workspace.json) is a
bare array of WorkspaceItems: an OceanQL query plus a required ``id`` and an
optional ``label``.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("oceanum.datamesh.query", reason="oceanum package required")

from oceanum.datamesh.query import Query  # noqa: E402

from oceanum_datamesh.workspace import ConnectionStore, connection_label  # noqa: E402


def _query(datasource: str) -> Query:
    return Query(datasource=datasource)


def test_empty_store_lists_nothing(tmp_path):
    store = ConnectionStore(tmp_path / "connections.json")
    assert store.list() == []


def test_add_assigns_id_and_persists(tmp_path):
    path = tmp_path / "connections.json"
    store = ConnectionStore(path)
    cid = store.add(_query("oceanum_wave_glob_era5"), label="Global wave hs")
    assert cid  # an id was assigned
    assert path.exists()
    reloaded = ConnectionStore(path).list()  # a fresh store reads it back
    assert len(reloaded) == 1
    assert reloaded[0].id == cid
    assert reloaded[0].datasource == "oceanum_wave_glob_era5"
    assert connection_label(reloaded[0]) == "Global wave hs"


def test_on_disk_format_is_workspace_array_schema(tmp_path):
    path = tmp_path / "connections.json"
    store = ConnectionStore(path)
    store.add(_query("oceanum_wave_glob_era5"), label="Global wave hs")
    raw = json.loads(path.read_text())
    # The workspace entity is a bare array of WorkspaceItems.
    assert isinstance(raw, list)
    item = raw[0]
    assert item["datasource"] == "oceanum_wave_glob_era5"  # OceanQL query field
    assert isinstance(item["id"], str) and item["id"]  # required WorkspaceItem id
    assert item["label"] == "Global wave hs"  # optional display label


def test_label_falls_back_to_datasource(tmp_path):
    store = ConnectionStore(tmp_path / "connections.json")
    store.add(_query("oceanum_wind_glob"))  # no label
    assert connection_label(store.list()[0]) == "oceanum_wind_glob"


def test_enveloped_spec_form_is_accepted_on_read(tmp_path):
    # Resilience: a {"spec": [...]} record should still load as connections.
    path = tmp_path / "connections.json"
    path.write_text(
        json.dumps({"name": "x", "spec": [{"datasource": "oceanum_wave", "id": "abc"}]})
    )
    conns = ConnectionStore(path).list()
    assert len(conns) == 1 and conns[0].id == "abc"


def test_get_update_remove(tmp_path):
    path = tmp_path / "connections.json"
    store = ConnectionStore(path)
    cid = store.add(_query("oceanum_wave_glob_era5"), label="Global wave hs")
    store.add(_query("oceanum_wind_glob"), label="Global wind")

    fetched = store.get(cid)
    assert fetched is not None and fetched.datasource == "oceanum_wave_glob_era5"

    store.update(cid, _query("oceanum_wave_glob_era5"), label="Renamed wave")
    assert connection_label(store.get(cid)) == "Renamed wave"
    assert len(store.list()) == 2  # update does not duplicate

    store.remove(cid)
    assert store.get(cid) is None
    assert len(store.list()) == 1
