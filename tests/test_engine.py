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
