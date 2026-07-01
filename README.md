# Oceanum Datamesh — QGIS plugin

Search, query, load and display [Oceanum Datamesh](https://oceanum.io) ocean and
environmental data directly inside QGIS.

The plugin adds a dockable panel where you can search the Datamesh catalogue,
inspect a dataset's metadata, filter it by variable / time / area, and load the
result as a native QGIS layer.

## What you get

Datamesh returns three kinds of container, each mapped to the natural QGIS layer:

| Datamesh result                        | QGIS layer                                   |
| -------------------------------------- | -------------------------------------------- |
| Gridded dataset (`xarray.Dataset`)     | **Raster** (GeoTIFF, one file per variable, one band per time step) |
| Station / scatter dataset (points sharing one dimension) | **Vector** points (GeoPackage) |
| Feature dataset (`geopandas.GeoDataFrame`) | **Vector** (GeoPackage)                 |
| Table (`pandas.DataFrame`)             | **Vector** points if it has lon/lat columns, otherwise an attribute **table** (CSV) |

All layers are created in EPSG:4326.

## Requirements

- **QGIS 4.x** (Qt6) or **QGIS ≥ 3.22** (Qt5). The plugin imports Qt through
  QGIS's `qgis.PyQt` compatibility layer and uses scoped enums, so it runs on
  both bindings. `supportsQt6` is declared in `metadata.txt`.
- The [`oceanum`](https://pypi.org/project/oceanum/) Python package, installed in
  the **QGIS** Python interpreter. The plugin detects when it is missing and
  offers to install it for you (`pip install --user oceanum`, with a
  `--break-system-packages` fallback on externally-managed distributions).
- A Datamesh access token.

## Installing

### 1. The plugin

Clone this repository and either symlink or zip the `oceanum_datamesh` folder
into your QGIS profile's plugin directory:

```bash
make deploy    # symlinks oceanum_datamesh into the QGIS plugins dir
# or
make zip       # builds oceanum_datamesh-<version>.zip for Plugins > Install from ZIP
```

Then enable **Oceanum Datamesh** in *Plugins → Manage and Install Plugins*.

### 2. The `oceanum` dependency

Open the plugin panel; if `oceanum` is not present it shows an **Install** button.
Alternatively install it yourself into the QGIS Python, e.g.:

```bash
python3 -m pip install --user oceanum        # add --break-system-packages if needed
```

> On very new Python builds where `zarr`'s compiled dependency `numcodecs` has no
> wheel, install oceanum's already-satisfied dependencies first and then
> `pip install --no-deps oceanum`. The plugin's normal (direct-download) query
> path does not need `zarr`.

### 3. Your token

Set it once via the panel's **Settings…** dialog (stored in QGIS settings for the
profile), or export it in the environment before launching QGIS:

```bash
export DATAMESH_TOKEN=...       # required
export DATAMESH_SERVICE=...     # optional, defaults to https://datamesh.oceanum.io
export DATAMESH_USER=...        # optional
```

## Using it

1. Click the Oceanum Datamesh toolbar icon to open the panel.
2. **Search** the catalogue by keyword (optionally restricted to the current map
   canvas extent).
3. Select a dataset to see its description, time range, bounds and variables.
4. Choose **variables**, a **time** window and an **area** (full extent, current
   canvas, or a manual bounding box).
5. Click **Load to map**.

Queries run on a background thread (`QgsTask`) so QGIS stays responsive; layers
are added when the download finishes.

### Query size

Datamesh streams very large requests lazily rather than as a direct download.
Pulling that much into QGIS is rarely intended, so the plugin stops with a clear
message asking you to narrow the time range, area or variable list. A filtered
query (a region and a time window) downloads directly and is what you normally
want.

## Development

```bash
make lint      # ruff
make test      # pytest (offline unit tests: converters + engine)
make deploy    # symlink into the QGIS plugins dir for live testing
```

The unit tests are offline (synthetic data, mocked connector) and need only the
scientific stack (`numpy`, `xarray`, `geopandas`, GDAL). Tests that touch QGIS
run against the QGIS Python bindings — set `QGIS_PY_PATH` if they are not on the
default path (QGIS 4 on Debian: `/usr/share/qgis/python`).

### Layout

```
oceanum_datamesh/
  __init__.py          classFactory (QGIS entry point)
  metadata.txt         plugin manifest (supportsQt6=True)
  plugin.py            QGIS GUI wiring (toolbar, menu, dock)
  engine.py            oceanum client wrapper (search / metadata / query)   [no QGIS imports]
  converters.py        result -> GeoTIFF / GeoPackage / CSV                 [no QGIS imports]
  tasks.py             background QgsTask runner
  utils.py             extent transform + temp storage helpers
  dependencies.py      detect / install the oceanum package
  gui/                 dock panel + settings dialog
  resources/           icon
tests/                 offline pytest suite
```

`engine.py` and `converters.py` deliberately avoid importing QGIS, so the network
and data-conversion logic is testable standalone and safe to run off the GUI
thread.

## Licence

Apache License 2.0 — see [LICENSE](LICENSE). Copyright 2026 Oceanum / Dave Johnson.
