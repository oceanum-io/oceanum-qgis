# Copyright 2026 Oceanum / Dave Johnson
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Convert Datamesh query results into files that QGIS can load as layers.

The functions here deliberately contain **no QGIS imports** so they can run in a
background thread (and be unit-tested) without touching the GUI. They turn the
three container types returned by ``oceanum`` into on-disk files:

* :class:`xarray.Dataset`         -> GeoTIFF raster(s) (one per data variable)
* :class:`geopandas.GeoDataFrame` -> GeoPackage vector layer
* :class:`pandas.DataFrame`       -> point GeoPackage (if x/y columns found)
                                     otherwise an aspatial CSV table

Each result is described by a :class:`LayerSpec`; the QGIS side is responsible
for turning a spec into an actual map layer on the main thread.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Optional

import numpy as np

# Common coordinate variable names, and the Datamesh standard-key aliases.
_X_NAMES = ("longitude", "lon", "long", "x", "nav_lon")
_Y_NAMES = ("latitude", "lat", "y", "nav_lat")


@dataclass
class LayerSpec:
    """A description of one map layer to be created from a written file."""

    kind: str  # "raster" | "vector" | "table"
    path: str
    name: str
    sublayer: Optional[str] = None  # GeoPackage layer name, if any


def safe_name(text: str, maxlen: int = 60) -> str:
    """Return a filesystem/layer-safe token derived from *text*."""
    token = re.sub(r"[^0-9A-Za-z._-]+", "_", str(text)).strip("_")
    return (token or "layer")[:maxlen]


# --------------------------------------------------------------------------- #
# Coordinate detection
# --------------------------------------------------------------------------- #
def _guess_xy(ds, coordinates: Optional[dict]) -> tuple[Optional[str], Optional[str]]:
    """Return the (x, y) coordinate names of an xarray dataset.

    *coordinates* is the Datamesh ``datasource.coordinates`` mapping of standard
    keys (``x``, ``y``, ``t`` ...) to variable names, used as the primary hint.
    """
    xname = yname = None
    if coordinates:
        xname = coordinates.get("x")
        yname = coordinates.get("y")
    coords = set(ds.coords) | set(ds.variables)
    if xname not in coords:
        xname = next((n for n in _X_NAMES if n in coords), None)
    if yname not in coords:
        yname = next((n for n in _Y_NAMES if n in coords), None)
    return xname, yname


def _geotransform(x: np.ndarray, y: np.ndarray):
    """Compute a north-up GDAL geotransform + whether the y axis needs flipping.

    Assumes a regular (evenly spaced) grid; spacing is taken from the mean step.
    Returns ``(geotransform, flip_y)``.
    """
    nx, ny = x.size, y.size
    dx = (x[-1] - x[0]) / (nx - 1) if nx > 1 else 1.0
    dy = (y[-1] - y[0]) / (ny - 1) if ny > 1 else 1.0
    ascending = bool(ny > 1 and y[-1] > y[0])
    if ascending:
        # Data runs south->north; flip so north is at the top (row 0).
        y_top = y[-1] + abs(dy) / 2.0
        pixel_h = -abs(dy)
    else:
        # Data already north->south (or single row).
        y_top = y[0] + abs(dy) / 2.0
        pixel_h = -abs(dy)
    x_left = x[0] - dx / 2.0
    gt = (float(x_left), float(dx), 0.0, float(y_top), 0.0, float(pixel_h))
    return gt, ascending


def dataset_to_rasters(
    ds,
    out_dir: str,
    variables: Optional[list] = None,
    coordinates: Optional[dict] = None,
    name_prefix: str = "",
    max_bands: int = 366,
) -> list[LayerSpec]:
    """Write each 2-D+ data variable of *ds* to a GeoTIFF and return specs.

    Extra dimensions (time, level, ...) become raster bands. ``max_bands`` caps
    the number of bands written per variable to keep files manageable.
    """
    from osgeo import gdal, osr

    gdal.UseExceptions()
    os.makedirs(out_dir, exist_ok=True)

    xname, yname = _guess_xy(ds, coordinates)
    if xname is None or yname is None:
        raise ValueError(
            "Could not identify longitude/latitude coordinates in the dataset; "
            "gridded raster export needs a regular lon/lat grid."
        )

    x = np.asarray(ds[xname].values)
    y = np.asarray(ds[yname].values)
    if x.ndim != 1 or y.ndim != 1:
        raise ValueError(
            "Dataset has 2-D (curvilinear) coordinates, which are not supported "
            "for raster export yet."
        )

    xdim = ds[xname].dims[0]
    ydim = ds[yname].dims[0]
    gt, flip_y = _geotransform(x, y)

    srs = osr.SpatialReference()
    srs.ImportFromEPSG(4326)
    proj_wkt = srs.ExportToWkt()

    if variables:
        var_list = [v for v in variables if v in ds.data_vars]
    else:
        var_list = list(ds.data_vars)

    specs: list[LayerSpec] = []
    for var in var_list:
        da = ds[var]
        if xdim not in da.dims or ydim not in da.dims:
            continue  # not a gridded field
        extra_dims = [d for d in da.dims if d not in (xdim, ydim)]
        da = da.transpose(*extra_dims, ydim, xdim)

        values = np.asarray(da.values, dtype="float32")
        ny, nx = values.shape[-2], values.shape[-1]
        stack = values.reshape((-1, ny, nx)) if extra_dims else values.reshape((1, ny, nx))

        nbands = stack.shape[0]
        truncated = nbands > max_bands
        if truncated:
            stack = stack[:max_bands]
            nbands = max_bands
        if flip_y:
            stack = stack[:, ::-1, :]

        band_labels = _band_labels(da, extra_dims, nbands)

        path = os.path.join(out_dir, safe_name(f"{name_prefix}{var}") + ".tif")
        drv = gdal.GetDriverByName("GTiff")
        out = drv.Create(
            path,
            nx,
            ny,
            nbands,
            gdal.GDT_Float32,
            options=["COMPRESS=DEFLATE", "TILED=YES", "BIGTIFF=IF_SAFER"],
        )
        out.SetGeoTransform(gt)
        out.SetProjection(proj_wkt)
        for i in range(nbands):
            band = out.GetRasterBand(i + 1)
            band.WriteArray(stack[i])
            band.SetNoDataValue(float("nan"))
            if band_labels:
                band.SetDescription(band_labels[i])
        out.FlushCache()
        out = None  # noqa: F841 (close dataset)

        label = da.attrs.get("long_name") or da.attrs.get("standard_name") or var
        units = da.attrs.get("units")
        display = f"{label} ({units})" if units else str(label)
        specs.append(LayerSpec("raster", path, display))
    if not specs:
        raise ValueError("No griddable variables found in the dataset.")
    return specs


def _band_labels(da, extra_dims, nbands) -> Optional[list]:
    """Human-readable band descriptions from the extra-dimension coordinates."""
    if not extra_dims:
        return None
    import itertools

    coord_values = []
    for d in extra_dims:
        if d in da.coords:
            coord_values.append([_fmt(v) for v in np.atleast_1d(da[d].values)])
        else:
            coord_values.append([str(i) for i in range(da.sizes[d])])
    labels = []
    for combo in itertools.product(*coord_values):
        labels.append(", ".join(f"{d}={v}" for d, v in zip(extra_dims, combo)))
        if len(labels) >= nbands:
            break
    return labels


def _fmt(value) -> str:
    try:
        return np.datetime_as_string(value, unit="s")
    except (ValueError, TypeError):
        return str(value)


# --------------------------------------------------------------------------- #
# Vector / table
# --------------------------------------------------------------------------- #
def geodataframe_to_gpkg(gdf, out_dir: str, name: str) -> LayerSpec:
    """Write a GeoDataFrame to a GeoPackage and return its spec."""
    os.makedirs(out_dir, exist_ok=True)
    g = gdf.copy()
    if g.crs is None:
        g = g.set_crs(4326, allow_override=True)
    g = _stringify_awkward_columns(g)
    if g.index.name or not g.index.equals(_default_index(g)):
        g = g.reset_index()
    layer = safe_name(name)
    path = os.path.join(out_dir, layer + ".gpkg")
    g.to_file(path, layer=layer, driver="GPKG")
    return LayerSpec("vector", path, name, sublayer=layer)


def dataframe_to_layer(
    df, out_dir: str, name: str, coordinates: Optional[dict] = None
) -> LayerSpec:
    """Write a plain DataFrame as a point layer (if x/y found) or CSV table."""
    os.makedirs(out_dir, exist_ok=True)
    frame = df.reset_index()
    xcol, ycol = _guess_frame_xy(frame, coordinates)
    if xcol and ycol:
        import geopandas as gpd

        frame = _stringify_awkward_columns(frame)
        geometry = gpd.points_from_xy(frame[xcol], frame[ycol])
        gdf = gpd.GeoDataFrame(frame, geometry=geometry, crs=4326)
        layer = safe_name(name)
        path = os.path.join(out_dir, layer + ".gpkg")
        gdf.to_file(path, layer=layer, driver="GPKG")
        return LayerSpec("vector", path, name, sublayer=layer)

    path = os.path.join(out_dir, safe_name(name) + ".csv")
    frame.to_csv(path, index=False)
    return LayerSpec("table", path, name)


def _guess_frame_xy(frame, coordinates):
    cols = {c.lower(): c for c in frame.columns}
    xcol = ycol = None
    if coordinates:
        xcol = coordinates.get("x") if coordinates.get("x") in frame.columns else None
        ycol = coordinates.get("y") if coordinates.get("y") in frame.columns else None
    if xcol is None:
        xcol = next((cols[n] for n in _X_NAMES if n in cols), None)
    if ycol is None:
        ycol = next((cols[n] for n in _Y_NAMES if n in cols), None)
    return xcol, ycol


def _stringify_awkward_columns(frame):
    """Cast columns GeoPackage cannot store (timedelta, object) to strings."""
    import pandas as pd

    # Name of the active geometry column, if this is a GeoDataFrame.
    geom_col = getattr(getattr(frame, "geometry", None), "name", None)
    for col in frame.columns:
        if col == geom_col:
            continue
        dtype = frame[col].dtype
        if pd.api.types.is_timedelta64_dtype(dtype):
            frame[col] = frame[col].astype(str)
        elif dtype == object and frame[col].map(lambda v: isinstance(v, (list, dict, tuple))).any():
            frame[col] = frame[col].astype(str)
    return frame


def _default_index(frame):
    import pandas as pd

    return pd.RangeIndex(start=0, stop=len(frame))


def _is_station_dataset(ds, xname, yname) -> bool:
    """True when x/y are 1-D and share a single dimension (scattered points).

    Datamesh station datasources come back as an xarray Dataset whose longitude
    and latitude are indexed by the *same* dimension (e.g. ``site``) rather than
    forming a regular grid, so they map to a point layer, not a raster.
    """
    if xname is None or yname is None:
        return False
    xd, yd = ds[xname].dims, ds[yname].dims
    return len(xd) == 1 and len(yd) == 1 and xd == yd


def dataset_stations_to_vector(
    ds,
    out_dir: str,
    name: str,
    variables: Optional[list] = None,
    coordinates: Optional[dict] = None,
) -> LayerSpec:
    """Write a station/scatter Dataset (points sharing one dim) to a GeoPackage.

    Size-1 extra dimensions (e.g. a single selected time) are squeezed; any
    remaining extra dimension is reduced to its first index so each site yields
    exactly one point feature.
    """
    import geopandas as gpd
    import numpy as np
    import pandas as pd

    os.makedirs(out_dir, exist_ok=True)
    xname, yname = _guess_xy(ds, coordinates)
    sdim = ds[xname].dims[0]

    reduced = ds.squeeze(drop=True)
    for dim in list(reduced.sizes):
        if dim != sdim and reduced.sizes[dim] > 1:
            reduced = reduced.isel({dim: 0})

    lon = np.asarray(ds[xname].values).ravel()
    lat = np.asarray(ds[yname].values).ravel()

    columns: dict = {}
    for cname in reduced.coords:
        if reduced[cname].dims == (sdim,) and cname not in (xname, yname):
            columns[str(cname)] = np.asarray(reduced[cname].values)

    var_list = variables or list(reduced.data_vars)
    for var in var_list:
        if var in reduced.data_vars and reduced[var].dims == (sdim,):
            columns[str(var)] = np.asarray(reduced[var].values).ravel()

    frame = pd.DataFrame(columns)
    frame = _stringify_awkward_columns(frame)
    gdf = gpd.GeoDataFrame(frame, geometry=gpd.points_from_xy(lon, lat), crs=4326)
    layer = safe_name(name)
    path = os.path.join(out_dir, layer + ".gpkg")
    gdf.to_file(path, layer=layer, driver="GPKG")
    return LayerSpec("vector", path, name, sublayer=layer)


def dataset_to_layers(
    ds,
    out_dir: str,
    name: str,
    variables: Optional[list] = None,
    coordinates: Optional[dict] = None,
) -> list[LayerSpec]:
    """Route an xarray Dataset to raster (grid) or vector (stations)."""
    xname, yname = _guess_xy(ds, coordinates)
    if _is_station_dataset(ds, xname, yname):
        return [dataset_stations_to_vector(ds, out_dir, name, variables, coordinates)]
    return dataset_to_rasters(
        ds,
        out_dir,
        variables=variables,
        coordinates=coordinates,
        name_prefix=f"{safe_name(name)}_",
    )


# --------------------------------------------------------------------------- #
# Dispatch
# --------------------------------------------------------------------------- #
def result_to_layers(
    result,
    out_dir: str,
    name: str,
    coordinates: Optional[dict] = None,
    variables: Optional[list] = None,
) -> list[LayerSpec]:
    """Turn any Datamesh query result into a list of :class:`LayerSpec`."""
    import xarray as xr

    if isinstance(result, xr.DataArray):
        result = result.to_dataset()
    if isinstance(result, xr.Dataset):
        return dataset_to_layers(
            result, out_dir, name, variables=variables, coordinates=coordinates
        )

    # geopandas is a subclass of pandas.DataFrame, so test it first.
    try:
        import geopandas as gpd

        if isinstance(result, gpd.GeoDataFrame):
            return [geodataframe_to_gpkg(result, out_dir, name)]
    except ImportError:
        pass

    import pandas as pd

    if isinstance(result, pd.DataFrame):
        return [dataframe_to_layer(result, out_dir, name, coordinates)]

    raise TypeError(f"Unsupported Datamesh result type: {type(result)!r}")
