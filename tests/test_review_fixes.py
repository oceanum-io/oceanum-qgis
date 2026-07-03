# Copyright 2026 Oceanum / Dave Johnson
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Regression tests for issues found in the connections/temporal PR review."""

from __future__ import annotations

import json

import numpy as np
import pytest

xr = pytest.importorskip("xarray", reason="xarray required")

from oceanum_datamesh import converters  # noqa: E402


def _grid(times=3, var="hs"):
    coords = {
        "lon": np.linspace(0, 2, 3),
        "lat": np.linspace(10, 12, 3),
        "time": np.array(
            [np.datetime64("2024-01-01") + np.timedelta64(i, "h") for i in range(times)]
        ),
    }
    data = np.random.rand(times, 3, 3).astype("float32")
    return xr.Dataset({var: (("time", "lat", "lon"), data)}, coords=coords)


def _single_site_station(times=2):
    time = np.array([np.datetime64("2024-01-01") + np.timedelta64(i, "h") for i in range(times)])
    return xr.Dataset(
        {"elev": (("time", "site"), np.random.rand(times, 1))},
        coords={
            "site": [0],
            "lon": ("site", np.array([1.5])),
            "lat": ("site", np.array([-3.0])),
            "time": time,
        },
    )


# --- bug 1: filename collision on long names -------------------------------- #
def test_raster_time_steps_get_unique_paths_for_long_names(tmp_path):
    long_prefix = "significant_wave_height_northwest_shelf_hindcast_"  # > 48 chars
    specs = converters.dataset_to_rasters(
        _grid(times=4, var="sea_surface_wave_significant_height"),
        str(tmp_path),
        name_prefix=long_prefix,
    )
    paths = [s.path for s in specs]
    assert len(paths) == 4
    assert len(set(paths)) == 4  # every step is a distinct file


# --- bug 2: single-site temporal station crash ------------------------------ #
def test_single_site_temporal_station_does_not_crash(tmp_path):
    gpd = pytest.importorskip("geopandas")
    spec = converters.dataset_stations_to_vector(
        _single_site_station(times=2), str(tmp_path), "one_site"
    )
    assert spec.time_field == "time"
    gdf = gpd.read_file(spec.path, layer=spec.sublayer)
    assert len(gdf) == 2  # 1 site x 2 times


# --- bug 6: resilient connection store -------------------------------------- #
def test_store_skips_bad_items_and_survives_corrupt_json(tmp_path):
    pytest.importorskip("oceanum.datamesh", reason="oceanum package required")
    from oceanum.datamesh import Query

    from oceanum_datamesh.workspace import ConnectionStore

    path = tmp_path / "connections.json"
    # One valid item + one invalid (datasource shorter than Query's min_length).
    path.write_text(
        json.dumps(
            [
                {"datasource": "oceanum_wave_glob", "id": "good", "label": "Waves"},
                {"datasource": "ab", "id": "bad"},
            ]
        )
    )
    store = ConnectionStore(path)
    conns = store.list()
    assert [c.id for c in conns] == ["good"]  # bad item skipped, good survives

    # add() still works despite the bad entry, and preserves it verbatim on disk.
    store.add(Query(datasource="oceanum_wind_glob"), label="Wind")
    assert {c.datasource for c in store.list()} == {"oceanum_wave_glob", "oceanum_wind_glob"}
    raw = json.loads(path.read_text())
    assert any(item["id"] == "bad" for item in raw)  # untouched item preserved

    # Corrupt JSON degrades to empty, not an exception.
    path.write_text('[{"datasource": "trunc')
    assert ConnectionStore(path).list() == []


# --- bug (altitude): geofilter passthrough ---------------------------------- #
def test_as_geofilter_passes_geojson_geometry_through():
    pytest.importorskip("oceanum.datamesh", reason="oceanum package required")
    from oceanum_datamesh.engine import DatameshEngine

    geometry = {"type": "Point", "coordinates": [1.0, 2.0]}
    # A GeoJSON geometry dict is a valid get_catalog input — must not be coerced.
    assert DatameshEngine._as_geofilter(geometry) is geometry


# --- cleanup: structural query-size guard ----------------------------------- #
def test_guard_query_size_uses_stage_size(monkeypatch):
    pytest.importorskip("oceanum.datamesh", reason="oceanum package required")
    from oceanum_datamesh.engine import _MAX_RESULT_BYTES, DatameshEngine, DatameshError

    engine = DatameshEngine(token="x")

    class _Stage:
        def __init__(self, size):
            self.size = size

    monkeypatch.setattr(engine, "stage", lambda q: _Stage(_MAX_RESULT_BYTES + 1))
    with pytest.raises(DatameshError, match="too large"):
        engine._guard_query_size(object())

    monkeypatch.setattr(engine, "stage", lambda q: _Stage(1000))
    engine._guard_query_size(object())  # small: no raise

    monkeypatch.setattr(engine, "stage", lambda q: None)
    engine._guard_query_size(object())  # empty stage: no raise


# --------------------------------------------------------------------------- #
# QGIS-side
# --------------------------------------------------------------------------- #
pytest.importorskip("qgis.core", reason="QGIS Python bindings required")

from qgis.testing import start_app  # noqa: E402

start_app()

from qgis.PyQt.QtCore import Qt  # noqa: E402
from qgis.testing.mocked import get_iface  # noqa: E402

from oceanum_datamesh import browser, layers  # noqa: E402
from oceanum_datamesh.gui.connection_dialog import ConnectionDialog  # noqa: E402
from oceanum_datamesh.utils import to_utc_qdatetime  # noqa: E402


# --- bug 3: timezone-safe time parsing -------------------------------------- #
def test_to_utc_qdatetime_is_utc_and_stable():
    dt = to_utc_qdatetime("2023-01-01T12:00:00")
    assert dt.timeSpec() == Qt.TimeSpec.UTC
    # .toUTC() must be a no-op (no offset shift), unlike a local-spec parse.
    assert dt.toUTC().toString(Qt.DateFormat.ISODate).startswith("2023-01-01T12:00:00")


# --- bug 8: single-step raster is visible under temporal navigation --------- #
def test_single_step_raster_range_contains_its_instant(tmp_path):
    specs = converters.dataset_to_rasters(_grid(times=1), str(tmp_path))
    assert specs[0].time_range[0] == specs[0].time_range[1]  # zero-length
    layer = layers.layer_from_spec(specs[0])
    layers.apply_temporal(layer, specs[0])
    trange = layer.temporalProperties().fixedTemporalRange()
    instant = to_utc_qdatetime(specs[0].time_range[0])
    assert trange.contains(instant)  # would be False for a half-open [b, b)


# --- bug 5: version-tolerant enum helpers ----------------------------------- #
def test_enum_helpers_resolve():
    from oceanum_datamesh.gui.connection_dialog import _geometry_types

    interp, classify = layers._shader_enums()
    assert interp is not None and classify is not None
    point_t, polygon_t = _geometry_types()
    assert point_t != polygon_t


# --- bug 4: coordinates fetched at load ------------------------------------- #
def test_coordinates_for_fetches_summary_map():
    class _Engine:
        def datasource_summary(self, _id):
            return {"coordinates": {"x": "lon_rho", "y": "lat_rho"}}

    assert browser._coordinates_for(_Engine(), "ds") == {"x": "lon_rho", "y": "lat_rho"}

    class _Broken:
        def datasource_summary(self, _id):
            raise RuntimeError("no metadata")

    assert browser._coordinates_for(_Broken(), "ds") == {}


# --- bug 7: saved vs feature geofilter slots are isolated -------------------- #
def test_geofilter_slots_do_not_alias():
    dialog = ConnectionDialog(get_iface(), engine=object())
    dialog._saved_geofilter = {"type": "feature", "geom": "SAVED"}
    dialog._feature_geofilter = {"type": "feature", "geom": "CAPTURED"}
    dialog.area_combo.insertItem(0, "Saved geometry filter", "saved")

    def area(key):
        # Route the combo without firing _on_area_changed, which for 'feature'
        # would open a modal capture dialog under the headless mock iface.
        dialog.area_combo.blockSignals(True)
        dialog._select_area(key)
        dialog.area_combo.blockSignals(False)

    area("saved")
    assert dialog._current_geofilter()["geom"] == "SAVED"
    area("feature")
    assert dialog._current_geofilter()["geom"] == "CAPTURED"
    # Switching back to the saved filter must still return the original (the two
    # slots no longer alias one attribute).
    area("saved")
    assert dialog._current_geofilter()["geom"] == "SAVED"

    # Changing datasource clears both slots and drops the saved combo entry.
    dialog._apply_datasource({"id": "ds2", "name": "DS2", "variables": []})
    assert dialog._saved_geofilter is None
    assert dialog._feature_geofilter is None
    assert dialog.area_combo.findData("saved") < 0
