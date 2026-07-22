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
    # Temporal metadata so QGIS can register the layer with the Temporal
    # Controller: a (begin, end) ISO range for a raster time step, or the name
    # of the datetime field for vectors.
    time_range: Optional[tuple] = None
    time_field: Optional[str] = None
    # Layer-tree group to place the layer in (e.g. one group per variable
    # holding its series of time-step rasters).
    group: Optional[str] = None
    # Global (min, max) of the variable across the whole series, so every
    # time-step layer is styled on the same colour scale.
    value_range: Optional[tuple] = None


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


def _guess_time(ds, coordinates: Optional[dict]) -> Optional[str]:
    """Return the time coordinate name of an xarray dataset, if any.

    Prefers the Datamesh ``t`` coordinate key, then falls back to any coordinate
    with a datetime dtype.
    """
    if coordinates:
        tname = coordinates.get("t")
        if tname in ds.coords:
            return tname
    for cname in ds.coords:
        if np.issubdtype(np.asarray(ds[cname].values).dtype, np.datetime64):
            return str(cname)
    return None


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


def _dateline_companion(gdal, path: str, x: np.ndarray, gt) -> Optional[str]:
    """Write a VRT exposing a raster's beyond-dateline part one world west.

    QGIS clips rasters at the CRS domain when reprojecting to a projected
    canvas (e.g. web mercator with a basemap), so pixels east of lon 180
    silently vanish. This VRT crops exactly those columns and locates them at
    their true geography west of the dateline, where every canvas CRS renders
    them (the crop matters: a shifted copy of the *whole* raster would itself
    stick out of the CRS domain and degrade reprojection near the seam). The
    native file keeps its 0-360 position for geographic canvases.
    """
    if x.size == 0 or float(np.max(x)) <= 180.0:
        return None
    x_left, dx = float(gt[0]), float(gt[1])
    src = gdal.Open(path)
    ncols, nrows = src.RasterXSize, src.RasterYSize
    del src
    # First pixel column at or east of the dateline (pixel-aligned crop).
    k0 = max(0, int(np.ceil((180.0 - x_left) / dx - 1e-9)))
    if k0 >= ncols:
        return None
    root = path[: -len(".tif")] if path.endswith(".tif") else path
    vrt_path = root + "_w360.vrt"
    vrt = gdal.Translate(vrt_path, path, format="VRT", srcWin=[k0, 0, ncols - k0, nrows])
    east_left = x_left + k0 * dx
    vrt.SetGeoTransform((east_left - 360.0, gt[1], gt[2], gt[3], gt[4], gt[5]))
    vrt.FlushCache()
    del vrt  # close to serialise the shifted geotransform into the .vrt
    return vrt_path


def dataset_to_rasters(
    ds,
    out_dir: str,
    variables: Optional[list] = None,
    coordinates: Optional[dict] = None,
    name_prefix: str = "",
    max_bands: int = 366,
) -> list[LayerSpec]:
    """Write the gridded data variables of *ds* to GeoTIFFs and return specs.

    A time dimension yields a *series of single-band temporal rasters* — one
    GeoTIFF per time step (all steps; overall volume is bounded upstream by the
    engine's query-size guard), each spec carrying its (begin, end) time range
    and a per-variable group name. Every non-time extra dimension (a band ``b``
    coordinate, levels, ...) becomes a band within the file; ``max_bands`` caps
    the band count per file only.
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

    tname = _guess_time(ds, coordinates)

    if variables:
        var_list = [v for v in variables if v in ds.data_vars]
    else:
        var_list = list(ds.data_vars)

    specs: list[LayerSpec] = []
    for var in var_list:
        da = ds[var]
        if xdim not in da.dims or ydim not in da.dims:
            continue  # not a gridded field

        label = da.attrs.get("long_name") or da.attrs.get("standard_name") or var
        units = da.attrs.get("units")
        display = f"{label} ({units})" if units else str(label)

        tdim = tname if (tname and tname in da.dims) else None
        if tdim is None:
            # Static field: any extra dims (a true ``b`` band coordinate,
            # levels, ...) become bands of a single file.
            path = os.path.join(out_dir, safe_name(f"{name_prefix}{var}") + ".tif")
            _write_geotiff(gdal, path, da, (xdim, ydim), gt, proj_wkt, flip_y, max_bands)
            specs.append(LayerSpec("raster", path, display))
            companion = _dateline_companion(gdal, path, x, gt)
            if companion:
                specs.append(LayerSpec("raster", companion, f"{display} (−360°)"))
            continue

        # Temporal field: one single-file raster per time step, grouped per
        # variable; each step covers [its time, the next time). All steps share
        # the variable's global value range so their colour scales match.
        tvalues = np.atleast_1d(ds[tname].values)
        nsteps = tvalues.size
        iso = [_fmt(v) for v in tvalues]
        value_range = _global_range(da)
        for i in range(nsteps):
            begin = iso[i]
            if i + 1 < tvalues.size:
                end = iso[i + 1]
            elif tvalues.size > 1:
                end = _fmt(tvalues[i] + (tvalues[i] - tvalues[i - 1]))
            else:
                end = begin
            sub = da.isel({tdim: i}, drop=True)
            # The step index guarantees a unique path even when a long
            # connection/variable name would truncate the timestamp away; the
            # timestamp is the layer's display name, not its filename.
            path = os.path.join(out_dir, f"{safe_name(f'{name_prefix}{var}', 48)}_{i:05d}.tif")
            _write_geotiff(gdal, path, sub, (xdim, ydim), gt, proj_wkt, flip_y, max_bands)
            specs.append(
                LayerSpec(
                    "raster",
                    path,
                    begin,
                    time_range=(begin, end),
                    group=display,
                    value_range=value_range,
                )
            )
            companion = _dateline_companion(gdal, path, x, gt)
            if companion:
                specs.append(
                    LayerSpec(
                        "raster",
                        companion,
                        f"{begin} (−360°)",
                        time_range=(begin, end),
                        group=display,
                        value_range=value_range,
                    )
                )
    if not specs:
        raise ValueError("No griddable variables found in the dataset.")
    return specs


def _write_geotiff(gdal, path, da, xydims, gt, proj_wkt, flip_y, max_bands) -> None:
    """Write a (possibly banded) DataArray slice to a GeoTIFF."""
    xdim, ydim = xydims
    extra_dims = [d for d in da.dims if d not in (xdim, ydim)]
    da = da.transpose(*extra_dims, ydim, xdim)

    values = np.asarray(da.values, dtype="float32")
    ny, nx = values.shape[-2], values.shape[-1]
    stack = values.reshape((-1, ny, nx))
    nbands = min(stack.shape[0], max_bands)
    stack = stack[:nbands]
    if flip_y:
        stack = stack[:, ::-1, :]

    band_labels = _band_labels(da, extra_dims, nbands)

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


def _global_range(da) -> Optional[tuple]:
    """(min, max) of a DataArray over all dims, or ``None`` if not finite.

    Computes on the native dtype — ``nanmin``/``nanmax`` need no float64 upcast,
    which would otherwise transiently double the memory of a large series.
    """
    values = np.asarray(da.values)
    if values.size == 0:
        return None
    with np.errstate(invalid="ignore"):
        vmin = float(np.nanmin(values))
        vmax = float(np.nanmax(values))
    if not (np.isfinite(vmin) and np.isfinite(vmax)):
        return None
    return (vmin, vmax)


# --------------------------------------------------------------------------- #
# Vector / table
# --------------------------------------------------------------------------- #
def geodataframe_to_gpkg(
    gdf, out_dir: str, name: str, coordinates: Optional[dict] = None
) -> LayerSpec:
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
    return LayerSpec(
        "vector", path, name, sublayer=layer, time_field=_guess_frame_time(g, coordinates)
    )


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
        return LayerSpec(
            "vector",
            path,
            name,
            sublayer=layer,
            time_field=_guess_frame_time(frame, coordinates),
        )

    path = os.path.join(out_dir, safe_name(name) + ".csv")
    frame.to_csv(path, index=False)
    return LayerSpec("table", path, name)


def _guess_frame_time(frame, coordinates) -> Optional[str]:
    """Name of the datetime column of a (Geo)DataFrame, if one exists.

    Prefers the Datamesh ``t`` coordinate key, then any datetime64 column.
    """
    import pandas as pd

    if coordinates:
        tcol = coordinates.get("t")
        if tcol in frame.columns:
            return str(tcol)
    for col in frame.columns:
        if pd.api.types.is_datetime64_any_dtype(frame[col]):
            return str(col)
    return None


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

    A time dimension is preserved as long-format rows (one feature per site and
    time step) with the time recorded in a field, so QGIS can register the layer
    as temporal. Any other extra dimension is reduced to its first index; the
    site and time dims are always kept (even when the site dim has length 1).
    """
    import geopandas as gpd
    import numpy as np
    import pandas as pd

    os.makedirs(out_dir, exist_ok=True)
    xname, yname = _guess_xy(ds, coordinates)
    tname = _guess_time(ds, coordinates)
    sdim = ds[xname].dims[0]

    # The time dimension (when separate from the site dim) is kept for
    # long-format output.
    tdim = None
    if tname is not None and tname in ds.coords:
        tdims = ds[tname].dims
        tdim = tdims[0] if len(tdims) == 1 and tdims[0] != sdim else None

    # Reduce every other dimension to its first index. Selecting (rather than
    # squeezing) keeps the site and time dims — and their lon/lat coords —
    # intact even when the site dim has length 1.
    reduced = ds
    for dim in list(ds.sizes):
        if dim not in (sdim, tdim):
            reduced = reduced.isel({dim: 0}, drop=True)

    if tdim is not None:
        # Long format: one row per (site, time). Coordinates on the site dim
        # (including lon/lat) are broadcast across time by to_dataframe().
        var_list = [
            v
            for v in (variables or list(reduced.data_vars))
            if v in reduced.data_vars and set(reduced[v].dims) <= {sdim, tdim}
        ]
        frame = reduced[var_list].to_dataframe().reset_index()
        frame = frame.drop(
            columns=[c for c in (sdim,) if c in frame.columns and c not in reduced.coords]
        )
        lon = frame[xname].to_numpy()
        lat = frame[yname].to_numpy()
        frame = frame.drop(columns=[xname, yname])
        time_field = str(tname)
    else:
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
        time_field = None

    frame = _stringify_awkward_columns(frame)
    gdf = gpd.GeoDataFrame(frame, geometry=gpd.points_from_xy(lon, lat), crs=4326)
    layer = safe_name(name)
    path = os.path.join(out_dir, layer + ".gpkg")
    gdf.to_file(path, layer=layer, driver="GPKG")
    return LayerSpec("vector", path, name, sublayer=layer, time_field=time_field)


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
            return [geodataframe_to_gpkg(result, out_dir, name, coordinates=coordinates)]
    except ImportError:
        pass

    import pandas as pd

    if isinstance(result, pd.DataFrame):
        return [dataframe_to_layer(result, out_dir, name, coordinates)]

    raise TypeError(f"Unsupported Datamesh result type: {type(result)!r}")
