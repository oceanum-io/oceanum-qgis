# Copyright 2026 Oceanum / Dave Johnson
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Temporal handling: time coordinates register layers with the controller."""

from __future__ import annotations

import numpy as np
import pytest

xr = pytest.importorskip("xarray", reason="xarray required")

from oceanum_datamesh import converters  # noqa: E402


def _grid(times=3, with_time=True):
    coords = {"lon": np.linspace(0, 2, 3), "lat": np.linspace(10, 12, 3)}
    dims = ("lat", "lon")
    shape = (3, 3)
    if with_time:
        coords["time"] = np.array(
            [np.datetime64("2024-01-01") + np.timedelta64(i, "h") for i in range(times)]
        )
        dims = ("time",) + dims
        shape = (times,) + shape
    data = np.random.rand(*shape).astype("float32")
    return xr.Dataset({"hs": (dims, data)}, coords=coords)


def _station(times=2):
    time = np.array([np.datetime64("2024-01-01") + np.timedelta64(i, "h") for i in range(times)])
    return xr.Dataset(
        {"elev": (("time", "site"), np.random.rand(times, 3))},
        coords={
            "site": np.arange(3),
            "lon": ("site", np.array([1.0, 2.0, 3.0])),
            "lat": ("site", np.array([-1.0, -2.0, -3.0])),
            "time": time,
        },
    )


def test_raster_time_series_becomes_single_band_rasters(tmp_path):
    specs = converters.dataset_to_rasters(_grid(times=3), str(tmp_path))
    # One single-band raster per time step, grouped per variable.
    assert len(specs) == 3
    assert all(s.group == specs[0].group for s in specs)
    assert [s.time_range[0][:13] for s in specs] == [
        "2024-01-01T00",
        "2024-01-01T01",
        "2024-01-01T02",
    ]
    # Each step ends where the next begins; the last extends by one step.
    assert specs[0].time_range[1] == specs[1].time_range[0]
    assert specs[2].time_range[1].startswith("2024-01-01T03")
    # Every file is single-band (times are layers, not bands).
    from osgeo import gdal

    for s in specs:
        assert gdal.Open(s.path).RasterCount == 1


def test_long_time_series_is_not_truncated(tmp_path):
    # More steps than the old 366 cap: every step must yield a raster.
    specs = converters.dataset_to_rasters(_grid(times=400), str(tmp_path))
    assert len(specs) == 400
    assert specs[-1].time_range[0].startswith("2024-01-17T15")  # 399 hours in


def test_static_raster_has_no_times(tmp_path):
    specs = converters.dataset_to_rasters(_grid(with_time=False), str(tmp_path))
    assert len(specs) == 1
    assert specs[0].time_range is None
    assert specs[0].group is None


def test_station_time_dim_becomes_long_format(tmp_path):
    spec = converters.dataset_stations_to_vector(_station(times=2), str(tmp_path), "st")
    assert spec.time_field == "time"
    import geopandas as gpd

    gdf = gpd.read_file(spec.path, layer=spec.sublayer)
    assert len(gdf) == 6  # 3 sites x 2 times
    assert "time" in gdf.columns


def test_dataframe_time_column_detected(tmp_path):
    import pandas as pd

    frame = pd.DataFrame(
        {
            "lon": [1.0, 2.0],
            "lat": [3.0, 4.0],
            "time": pd.to_datetime(["2024-01-01", "2024-01-02"]),
            "value": [1, 2],
        }
    )
    spec = converters.dataframe_to_layer(frame, str(tmp_path), "df")
    assert spec.kind == "vector"
    assert spec.time_field == "time"


# --------------------------------------------------------------------------- #
# QGIS-side registration
# --------------------------------------------------------------------------- #
qgis_core = pytest.importorskip("qgis.core", reason="QGIS Python bindings required")

from qgis.testing import start_app  # noqa: E402

start_app()

from qgis.core import Qgis  # noqa: E402

from oceanum_datamesh import layers  # noqa: E402


def test_raster_time_steps_get_fixed_temporal_ranges(tmp_path):
    specs = converters.dataset_to_rasters(_grid(times=3), str(tmp_path))
    for spec in specs:
        layer = layers.layer_from_spec(spec)
        assert layer.isValid()
        assert layer.bandCount() == 1  # a series of single-band rasters
        layers.apply_temporal(layer, spec)
        props = layer.temporalProperties()
        assert props.isActive()
        assert props.mode() == Qgis.RasterTemporalMode.FixedTemporalRange
    first = layers.layer_from_spec(specs[0])
    layers.apply_temporal(first, specs[0])
    trange = first.temporalProperties().fixedTemporalRange()
    assert trange.begin().toString("yyyy-MM-dd hh") == "2024-01-01 00"
    assert trange.end().toString("yyyy-MM-dd hh") == "2024-01-01 01"


def test_grouped_series_lands_in_layer_tree_group(tmp_path):
    from qgis.core import QgsProject

    specs = converters.dataset_to_rasters(_grid(times=3), str(tmp_path))
    added, failed = layers.add_layer_specs(specs)
    try:
        assert (added, failed) == (3, [])
        group = QgsProject.instance().layerTreeRoot().findGroup(specs[0].group)
        assert group is not None
        assert len(group.findLayers()) == 3
    finally:
        QgsProject.instance().clear()


def test_vector_layer_gets_instant_from_field(tmp_path):
    spec = converters.dataset_stations_to_vector(_station(times=2), str(tmp_path), "st")
    layer = layers.layer_from_spec(spec)
    assert layer.isValid()
    layers.apply_temporal(layer, spec)
    props = layer.temporalProperties()
    assert props.isActive()
    assert props.mode() == Qgis.VectorTemporalMode.FeatureDateTimeInstantFromField
    assert props.startField() == "time"


def test_static_layers_stay_atemporal(tmp_path):
    spec = converters.dataset_to_rasters(_grid(with_time=False), str(tmp_path))[0]
    layer = layers.layer_from_spec(spec)
    layers.apply_temporal(layer, spec)
    assert not layer.temporalProperties().isActive()


def test_series_shares_a_global_colour_scale(tmp_path):
    specs = converters.dataset_to_rasters(_grid(times=3), str(tmp_path))
    # The converter stamps one global (min, max) on every step of the series.
    assert all(s.value_range == specs[0].value_range for s in specs)
    vmin, vmax = specs[0].value_range
    assert vmin < vmax
    # Each layer gets the same pseudocolour renderer bounds.
    for spec in specs:
        layer = layers.layer_from_spec(spec)
        layers.apply_shared_style(layer, spec)
        renderer = layer.renderer()
        assert renderer.type() == "singlebandpseudocolor"
        assert renderer.classificationMin() == pytest.approx(vmin)
        assert renderer.classificationMax() == pytest.approx(vmax)


def test_static_raster_keeps_default_style(tmp_path):
    spec = converters.dataset_to_rasters(_grid(with_time=False), str(tmp_path))[0]
    layer = layers.layer_from_spec(spec)
    before = layer.renderer().type()
    layers.apply_shared_style(layer, spec)
    assert layer.renderer().type() == before  # untouched
