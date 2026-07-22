# Copyright 2026 Oceanum / Dave Johnson
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Offline unit tests for the data -> file converters (no QGIS required)."""

from __future__ import annotations

import numpy as np
import pytest

from oceanum_datamesh import converters


@pytest.fixture
def grid_dataset():
    xr = pytest.importorskip("xarray")
    return xr.Dataset(
        {
            "hs": (
                ("time", "latitude", "longitude"),
                np.arange(2 * 4 * 5, dtype="float32").reshape(2, 4, 5),
            )
        },
        coords={
            "time": np.array(["2025-01-01", "2025-01-02"], dtype="datetime64[ns]"),
            "latitude": np.linspace(-40, -34, 4),  # ascending -> should flip
            "longitude": np.linspace(170, 178, 5),
        },
    )


@pytest.fixture
def station_dataset():
    xr = pytest.importorskip("xarray")
    return xr.Dataset(
        {
            "elevation": (("site",), np.array([1.0, 2.0, 3.0], dtype="float32")),
            "hs": (("time", "site"), np.ones((1, 3), dtype="float32")),
        },
        coords={
            "time": np.array(["2020-01-01"], dtype="datetime64[ns]"),
            "longitude": (("site",), np.array([170.0, 171.0, 172.0])),
            "latitude": (("site",), np.array([-40.0, -41.0, -42.0])),
            "site": (("site",), ["A", "B", "C"]),
        },
    )


COORDS = {"x": "longitude", "y": "latitude", "t": "time"}


def test_safe_name():
    assert converters.safe_name("Wave Hs (m)!") == "Wave_Hs_m"
    assert converters.safe_name("") == "layer"


def test_geotransform_flips_ascending_lat():
    x = np.linspace(170, 178, 5)
    y_asc = np.linspace(-40, -34, 4)
    gt, flip = converters._geotransform(x, y_asc)
    assert flip is True
    assert gt[1] > 0  # positive pixel width
    assert gt[5] < 0  # north-up (negative pixel height)


def test_grid_dataset_to_geotiff(grid_dataset, tmp_path):
    gdal = pytest.importorskip("osgeo.gdal", reason="GDAL required")
    specs = converters.dataset_to_layers(
        grid_dataset, str(tmp_path), "wave", variables=["hs"], coordinates=COORDS
    )
    # Two time steps -> a series of two single-band temporal rasters.
    assert len(specs) == 2
    for spec in specs:
        assert spec.kind == "raster"
        assert spec.time_range is not None
        ds = gdal.Open(spec.path)
        assert ds is not None
        assert ds.RasterXSize == 5 and ds.RasterYSize == 4
        assert ds.RasterCount == 1
        assert ds.GetProjection()  # CRS set


def test_station_dataset_to_points(station_dataset, tmp_path):
    ogr = pytest.importorskip("osgeo.ogr", reason="OGR required")
    specs = converters.dataset_to_layers(
        station_dataset, str(tmp_path), "stations", coordinates=COORDS
    )
    assert len(specs) == 1
    assert specs[0].kind == "vector"
    source = ogr.Open(specs[0].path)
    layer = source.GetLayer()
    assert layer.GetFeatureCount() == 3
    assert "Point" in ogr.GeometryTypeToName(layer.GetGeomType())


def test_geodataframe_to_gpkg(tmp_path):
    gpd = pytest.importorskip("geopandas")
    gdf = gpd.GeoDataFrame(
        {"name": ["a", "b"]},
        geometry=gpd.points_from_xy([170.0, 171.0], [-40.0, -39.0]),
        crs=4326,
    )
    spec = converters.geodataframe_to_gpkg(gdf, str(tmp_path), "pts")
    assert spec.kind == "vector"
    ogr = pytest.importorskip("osgeo.ogr")
    assert ogr.Open(spec.path).GetLayer().GetFeatureCount() == 2


def test_dataframe_with_lonlat_to_points(tmp_path):
    pd = pytest.importorskip("pandas")
    pytest.importorskip("geopandas")
    df = pd.DataFrame({"lon": [170.0, 171.0], "lat": [-40.0, -41.0], "v": [1, 2]})
    spec = converters.dataframe_to_layer(df, str(tmp_path), "obs")
    assert spec.kind == "vector"


def test_dataframe_without_geometry_to_table(tmp_path):
    pd = pytest.importorskip("pandas")
    df = pd.DataFrame(
        {"value": [1.0, 2.0]},
        index=pd.to_datetime(["2020-01-01", "2020-01-02"]),
    )
    spec = converters.dataframe_to_layer(df, str(tmp_path), "series")
    assert spec.kind == "table"
    assert spec.path.endswith(".csv")


def test_result_to_layers_rejects_unknown_type(tmp_path):
    with pytest.raises(TypeError):
        converters.result_to_layers(object(), str(tmp_path), "x")


def test_dateline_crossing_raster_gets_cropped_companion(tmp_path):
    xr = pytest.importorskip("xarray")
    pytest.importorskip("osgeo.gdal")
    from osgeo import gdal

    ds = xr.Dataset(
        {"hs": (("latitude", "longitude"), np.ones((3, 21), dtype="float32"))},
        coords={
            "latitude": np.linspace(0, 2, 3),
            "longitude": np.arange(170.0, 190.5, 1.0),  # crosses the dateline
        },
    )
    specs = converters.dataset_to_rasters(ds, str(tmp_path))
    assert len(specs) == 2
    assert specs[0].path.endswith(".tif")
    assert specs[1].path.endswith("_w360.vrt")
    vrt = gdal.Open(specs[1].path)
    gt = vrt.GetGeoTransform()
    # Cropped to the beyond-dateline columns, located one world west: the
    # first pixel edge at or east of lon 180 is 180.5, shifted to -179.5.
    assert gt[0] == pytest.approx(-179.5)
    assert vrt.RasterXSize == 10
    assert vrt.ReadAsArray() is not None


def test_temporal_dateline_companions_share_group_and_range(grid_dataset, tmp_path):
    ds = grid_dataset.assign_coords(longitude=np.linspace(176, 184, 5))
    specs = converters.dataset_to_rasters(ds, str(tmp_path))
    tifs = [s for s in specs if s.path.endswith(".tif")]
    vrts = [s for s in specs if s.path.endswith(".vrt")]
    assert len(tifs) == 2 and len(vrts) == 2  # one companion per time step
    for tif, vrt in zip(tifs, vrts):
        assert vrt.group == tif.group
        assert vrt.time_range == tif.time_range
        assert vrt.value_range == tif.value_range


def test_non_crossing_raster_has_no_companion(tmp_path):
    xr = pytest.importorskip("xarray")
    ds = xr.Dataset(
        {"hs": (("latitude", "longitude"), np.ones((3, 5), dtype="float32"))},
        coords={"latitude": np.linspace(0, 2, 3), "longitude": np.linspace(150, 158, 5)},
    )
    specs = converters.dataset_to_rasters(ds, str(tmp_path))
    assert len(specs) == 1
