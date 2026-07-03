# Copyright 2026 Oceanum / Dave Johnson
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Offline tests for the map-compatibility gate and query coercion."""

from __future__ import annotations

import pytest

from oceanum_datamesh.engine import DatameshEngine, map_compatibility


@pytest.mark.parametrize(
    "coordkeys",
    [
        {"t": "time", "x": "longitude", "y": "latitude"},  # grid
        {"t": "time", "x": "lon", "y": "lat", "s": "station"},  # station points
        {"longitude": "x", "latitude": "y", "time": "t"},  # inverse orientation
    ],
)
def test_xy_datasets_are_compatible(coordkeys):
    ok, reason = map_compatibility(coordkeys)
    assert ok is True
    assert reason == "x/y"


def test_geometry_dataset_is_compatible():
    ok, reason = map_compatibility({"g": "geometry", "t": "time"})
    assert ok is True
    assert reason == "geometry"


@pytest.mark.parametrize("coordkeys", [{"t": "time"}, {}, None])
def test_non_spatial_is_incompatible(coordkeys):
    ok, reason = map_compatibility(coordkeys)
    assert ok is False
    assert "no spatial coordinates" in reason.lower()


def test_as_geofilter_coerces_bbox_dict():
    pytest.importorskip("oceanum.datamesh", reason="oceanum package required")
    from oceanum.datamesh.query import GeoFilter

    # A plain dict is validated into the model (get_catalog would otherwise
    # read it as GeoJSON and fail with "Unknown geometry type bbox").
    coerced = DatameshEngine._as_geofilter({"type": "bbox", "geom": [1, 2, 3, 4]})
    assert isinstance(coerced, GeoFilter)
    assert list(coerced.geom) == [1.0, 2.0, 3.0, 4.0]
    # None and existing models pass through untouched.
    assert DatameshEngine._as_geofilter(None) is None
    assert DatameshEngine._as_geofilter(coerced) is coerced


def test_as_query_drops_unknown_keys_and_empties():
    pytest.importorskip("oceanum.datamesh", reason="oceanum package required")
    from oceanum.datamesh import Query

    engine = DatameshEngine(token="x")
    query = engine._as_query(
        {
            "datasource": "oceanum_wave_glob_era5",
            "variables": ["hs"],
            "geofilter": None,  # dropped (empty)
            "use_dask": True,  # dropped (not a Query field)
            "junk": 123,  # dropped (unknown)
        }
    )
    assert isinstance(query, Query)
    assert query.datasource == "oceanum_wave_glob_era5"
    assert query.variables == ["hs"]
    # Pass-through: an existing Query is returned unchanged.
    assert engine._as_query(query) is query
