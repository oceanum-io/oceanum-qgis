# Copyright 2026 Oceanum / Dave Johnson
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Unit tests for the Browser provider's pure helpers (needs QGIS bindings)."""

from __future__ import annotations

import pytest

pytest.importorskip("qgis.core", reason="QGIS Python bindings required")

from oceanum_datamesh import browser  # noqa: E402


def test_is_gridded_true_for_xy_grid():
    assert browser._is_gridded({"t": "time", "x": "longitude", "y": "latitude"}) is True


@pytest.mark.parametrize(
    "coords",
    [
        {"t": "time", "x": "lon", "y": "lat", "i": "site"},  # station index
        {"t": "time", "x": "lon", "y": "lat", "s": "station"},  # station
        {"g": "geometry", "t": "time"},  # feature geometry
        {"t": "#Timestamp"},  # timeseries, no x/y
    ],
)
def test_is_gridded_false_for_non_grids(coords):
    assert browser._is_gridded(coords) is False
