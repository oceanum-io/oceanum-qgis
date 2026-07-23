# Copyright 2026 Oceanum / Dave Johnson
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Offline unit tests for DatameshEngine (no network, no QGIS)."""

from __future__ import annotations

import warnings

import pytest

from oceanum_datamesh.engine import DatameshEngine, DatameshError


class FakeConnector:
    """Records query kwargs and returns a scripted result."""

    def __init__(self, result=None, warn_too_large=False, raises=None):
        self.result = result
        self.warn_too_large = warn_too_large
        self.raises = raises
        self.calls: list[dict] = []

    def query(self, **kwargs):
        self.calls.append(kwargs)
        if self.raises is not None:
            raise self.raises
        if self.warn_too_large:
            warnings.warn(
                "Query is too large for direct access, using lazy access with dask",
                stacklevel=2,
            )
        return self.result


def _engine_with(connector) -> DatameshEngine:
    engine = DatameshEngine(token="dummy")
    engine._connector = connector  # bypass real connect()
    return engine


def test_query_passes_filters_through():
    fake = FakeConnector(result="RESULT")
    engine = _engine_with(fake)
    spec = {
        "datasource": "ds1",
        "variables": ["hs", "tp"],
        "timefilter": {"type": "range", "times": ["2020", "2021"]},
        "geofilter": {"type": "bbox", "geom": [1, 2, 3, 4]},
        "use_dask": False,
    }
    assert engine.query(spec) == "RESULT"
    call = fake.calls[0]
    assert call["datasource"] == "ds1"
    assert call["variables"] == ["hs", "tp"]
    assert call["timefilter"]["times"] == ["2020", "2021"]
    assert call["geofilter"]["geom"] == [1, 2, 3, 4]
    assert call["use_dask"] is False


def test_query_omits_absent_filters():
    fake = FakeConnector(result="R")
    _engine_with(fake).query({"datasource": "ds"})
    call = fake.calls[0]
    assert "variables" not in call
    assert "timefilter" not in call
    assert "geofilter" not in call


def test_query_none_result_returns_none():
    engine = _engine_with(FakeConnector(result=None))
    assert engine.query({"datasource": "ds"}) is None


def test_query_too_large_warning_becomes_friendly_error():
    engine = _engine_with(FakeConnector(result="R", warn_too_large=True))
    with pytest.raises(DatameshError, match="too large"):
        engine.query({"datasource": "ds"})


def test_query_zarrclient_error_becomes_friendly_error():
    engine = _engine_with(
        FakeConnector(raises=TypeError("Unsupported type for store_like: 'ZarrClient'"))
    )
    with pytest.raises(DatameshError, match="too large"):
        engine.query({"datasource": "ds"})


def test_query_other_error_wrapped():
    engine = _engine_with(FakeConnector(raises=ValueError("boom")))
    with pytest.raises(DatameshError, match="Query failed"):
        engine.query({"datasource": "ds"})


def test_connect_without_token_raises():
    engine = DatameshEngine(token=None)
    engine._token = None
    with pytest.raises(DatameshError, match="token"):
        engine.connect()


class _FakeDatasource:
    id = "ds1"
    name = "Test dataset"
    description = "desc"
    tags = ["a"]
    tstart = None
    tend = None
    bounds = [0, -10, 20, 10]
    coordinates = {"x": "lon", "y": "lat"}
    variables = ["hs", "tp"]
    driver = "onzarr"
    details = None


def test_summarize_datasource():
    summary = DatameshEngine._summarize_datasource(_FakeDatasource())
    assert summary["id"] == "ds1"
    assert summary["variables"] == ["hs", "tp"]
    assert summary["bounds"] == [0, -10, 20, 10]
    assert summary["coordinates"] == {"x": "lon", "y": "lat"}


class _SchemaDsrc:
    id = "ds1"
    name = "DS 1"
    coordinates = {}
    bounds = None
    variables = {
        "hs": {"attrs": {"long_name": "Significant wave height", "units": "m"}},
        "tp": {"attrs": {"units": "s"}},
        "dpm": {"attrs": {"standard_name": "wave_direction"}},
        "u10": None,
    }


def test_summary_extracts_variable_names_from_attrs():
    summary = DatameshEngine._summarize_datasource(_SchemaDsrc())
    assert summary["variables"] == ["hs", "tp", "dpm", "u10"]
    assert summary["variable_names"] == {
        "hs": "Significant wave height",
        "dpm": "wave_direction",
    }


def test_variable_name_preference_order():
    from oceanum_datamesh.engine import _variable_name

    assert _variable_name({"long_name": "L", "standard_name": "S"}) == "L"
    assert _variable_name({"standard_name": "S", "nice_name": "N"}) == "S"
    assert _variable_name({"nice_name": "N"}) == "N"
    assert _variable_name({"units": "m"}) is None
    assert _variable_name(None) is None


def test_variable_name_failure_keeps_ids():
    class _BadItems(dict):  # listing works, metadata access explodes
        def items(self):
            raise RuntimeError("boom")

    class _Dsrc:
        id = "ds2"
        coordinates = {}
        bounds = None
        variables = _BadItems({"hs": None, "tp": None})

    summary = DatameshEngine._summarize_datasource(_Dsrc())
    assert summary["variables"] == ["hs", "tp"]
    assert summary["variable_names"] == {}


def test_summary_includes_datasource_geometry():
    class _Geom:
        __geo_interface__ = {
            "type": "Polygon",
            "coordinates": (((0, 0), (10, 0), (10, 10), (0, 10), (0, 0)),),
        }

    class _Dsrc:
        id = "ds3"
        coordinates = {}
        bounds = [0, 0, 10, 10]
        variables = None
        geometry = _Geom()

    summary = DatameshEngine._summarize_datasource(_Dsrc())
    assert summary["geometry"]["type"] == "Polygon"
    assert summary["bounds"] == [0, 0, 10, 10]


class _FrameDsrc:
    def __init__(self, bounds):
        self.bounds = bounds


class _WrapConnector:
    """Returns a grid whose values equal the source longitudes queried."""

    def __init__(self, bounds):
        self._bounds = bounds
        self.bboxes: list[list] = []

    def get_datasource(self, _id):
        return _FrameDsrc(self._bounds)

    def query(self, **kwargs):
        import numpy as np
        import xarray as xr

        geom = kwargs["geofilter"]["geom"]
        self.bboxes.append(list(geom))
        lons = np.arange(geom[0], geom[2] + 0.5, 1.0)
        return xr.Dataset(
            {"hs": (("latitude", "longitude"), np.tile(lons, (2, 1)))},
            coords={"latitude": [0.0, 1.0], "longitude": lons},
        )


def test_meridian_wrap_bbox_splits_and_glues():
    import numpy as np

    pytest.importorskip("xarray")
    connector = _WrapConnector(bounds=[0.0, -70.0, 360.0, 70.0])
    engine = _engine_with(connector)
    result = engine.query(
        {"datasource": "d", "geofilter": {"type": "bbox", "geom": [-10, 0, 10, 5]}}
    )
    assert connector.bboxes == [[350.0, 0.0, 360.0, 5.0], [0.0, 0.0, 10.0, 5.0]]
    lons = result["longitude"].values
    assert lons[0] == -10.0 and lons[-1] == 10.0
    assert np.all(np.diff(lons) > 0)
    assert lons.size == 21  # seam column (360 == 0) deduplicated
    # Values preserved through the shift: lon -5 came from source lon 355.
    assert float(result["hs"].sel(longitude=-5.0).isel(latitude=0)) == 355.0


def test_dateline_wrap_bbox_on_pm180_datasource():
    import numpy as np

    pytest.importorskip("xarray")
    connector = _WrapConnector(bounds=[-180.0, -70.0, 180.0, 70.0])
    engine = _engine_with(connector)
    result = engine.query(
        {"datasource": "d", "geofilter": {"type": "bbox", "geom": [170, 0, 190, 5]}}
    )
    assert connector.bboxes == [[170.0, 0.0, 180.0, 5.0], [-180.0, 0.0, -170.0, 5.0]]
    lons = result["longitude"].values
    assert lons[0] == 170.0 and lons[-1] == 190.0
    assert np.all(np.diff(lons) > 0)
    assert lons.size == 21


def test_in_frame_bbox_runs_single_query():
    pytest.importorskip("xarray")
    connector = _WrapConnector(bounds=[0.0, -70.0, 360.0, 70.0])
    engine = _engine_with(connector)
    engine.query({"datasource": "d", "geofilter": {"type": "bbox", "geom": [170, 0, 190, 5]}})
    assert connector.bboxes == [[170, 0, 190, 5]]  # 0-360 frame holds it in one piece
