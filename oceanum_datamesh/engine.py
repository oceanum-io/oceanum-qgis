# Copyright 2026 Oceanum / Dave Johnson
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Thin wrapper around the ``oceanum`` Datamesh client.

Kept free of QGIS imports so it can run in a background thread and be tested
standalone. ``oceanum`` itself is imported lazily so the plugin can load and
prompt for installation when the dependency is missing.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


class DatameshError(Exception):
    """Any failure talking to Datamesh, with a user-friendly message."""


# Above this, Datamesh streams results lazily rather than as a direct download;
# pulling that much into QGIS is rarely intended, so we ask the user to narrow.
_MAX_RESULT_BYTES = 1_000_000_000  # 1 GB
_TOO_LARGE_MSG = (
    "This query is too large for direct download. Narrow the time range, "
    "bounding box or number of variables and try again"
)


def _iso(value) -> Optional[str]:
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _variable_name(attrs) -> Optional[str]:
    """Pick a human-readable variable name from schema attributes.

    Prefers the CF ``long_name`` then ``standard_name``, falling back to any
    attribute whose key contains ``name``.
    """
    if not isinstance(attrs, dict):
        return None
    for key in ("long_name", "standard_name"):
        value = attrs.get(key)
        if value:
            return str(value)
    for key, value in attrs.items():
        if "name" in str(key).lower() and value:
            return str(value)
    return None


# Canonical Datamesh coordinate axis keys (oceanum.datamesh.datasource.Coordinates):
# ``x`` easting, ``y`` northing, ``g`` an abstract feature geometry.
_AXIS_X, _AXIS_Y, _AXIS_GEOM = "x", "y", "g"


def map_compatibility(coordkeys) -> tuple[bool, str]:
    """Decide whether a staged query can be shown on the QGIS map.

    A Datamesh view is map-compatible when it is georeferenced: either it
    exposes both an ``x`` and ``y`` coordinate (grids and station points) or an
    abstract geometry coordinate ``g`` (feature collections). Pure time series
    and plain tables expose neither and cannot be placed on the map.

    ``coordkeys`` is the ``Stage.coordkeys`` mapping; we inspect both its keys
    and values so the check is robust to the mapping orientation.
    """
    tokens = set()
    for key, value in dict(coordkeys or {}).items():
        tokens.add(str(getattr(key, "value", key)).lower())
        tokens.add(str(getattr(value, "value", value)).lower())
    if _AXIS_GEOM in tokens:
        return True, "geometry"
    if _AXIS_X in tokens and _AXIS_Y in tokens:
        return True, "x/y"
    return (
        False,
        "This view has no spatial coordinates (it needs x and y, or a geometry) "
        "so it cannot be shown on the map. Choose a different datasource or "
        "variables.",
    )


class DatameshEngine:
    """Search the catalog, inspect datasources and run queries.

    Connection parameters default to the standard Datamesh environment
    variables (``DATAMESH_TOKEN``, ``DATAMESH_SERVICE``, ``DATAMESH_USER``).
    """

    def __init__(self, token=None, service=None, user=None):
        self._token = token or os.environ.get("DATAMESH_TOKEN")
        self._service = service or os.environ.get("DATAMESH_SERVICE")
        self._user = user or os.environ.get("DATAMESH_USER")
        self._connector = None

    # -- connection -------------------------------------------------------- #
    @property
    def has_token(self) -> bool:
        return bool(self._token)

    def connect(self):
        """Return a live ``oceanum`` connector, constructing it on first use.

        Constructing the connector performs a metadata request, so this is also
        where an invalid token or unreachable service surfaces.
        """
        if self._connector is not None:
            return self._connector
        if not self._token:
            raise DatameshError(
                "No Datamesh token configured. Set the DATAMESH_TOKEN environment "
                "variable or enter a token in the plugin settings."
            )
        try:
            from oceanum.datamesh import Connector
        except ImportError as exc:  # pragma: no cover - exercised via GUI path
            raise DatameshError(
                "The 'oceanum' Python package is not installed. Use the plugin's "
                "'Install dependencies' button or run: pip install oceanum"
            ) from exc

        kwargs = {"token": self._token}
        if self._service:
            kwargs["service"] = self._service
        if self._user:
            kwargs["user"] = self._user
        try:
            self._connector = Connector(**kwargs)
        except Exception as exc:  # noqa: BLE001 - surface any client/network error
            raise DatameshError(f"Could not connect to Datamesh: {exc}") from exc
        return self._connector

    # -- catalog search ---------------------------------------------------- #
    @staticmethod
    def _as_geofilter(geofilter):
        """Coerce a GeoFilter *spec* dict into an OceanQL ``GeoFilter`` model.

        ``get_catalog`` reads a raw dict as a GeoJSON *geometry*, so a GeoFilter
        spec like ``{"type": "bbox", ...}`` fails with "Unknown geometry type
        bbox" unless validated into the model first. Only dicts whose ``type``
        is a GeoFilter type are coerced; a plain GeoJSON geometry dict (a valid
        ``get_catalog`` input) is passed through untouched.
        """
        if not isinstance(geofilter, dict):
            return geofilter
        from oceanum.datamesh.query import GeoFilter, GeoFilterType

        if geofilter.get("type") not in {t.value for t in GeoFilterType}:
            return geofilter
        return GeoFilter(**geofilter)

    def search(self, text=None, geofilter=None, timefilter=None, limit=200) -> list[dict]:
        """Return a list of catalog summary dicts matching the filters."""
        cat = self.connect().get_catalog(
            search=text or None,
            geofilter=self._as_geofilter(geofilter),
            timefilter=timefilter,
            limit=limit,
        )
        features = {}
        raw = getattr(cat, "_geojson", None)
        if raw is not None:
            features = {f.id: f for f in raw.features}

        results = []
        for _id in cat.ids:
            feature = features.get(_id)
            if feature is not None:
                results.append(self._summarize_feature(_id, feature))
            else:  # fallback: build a full Datasource (an extra parse, not a request)
                try:
                    results.append(self._summarize_datasource(cat[_id]))
                except Exception:  # noqa: BLE001
                    results.append({"id": _id, "name": _id})
        return results

    def datasource(self, datasource_id: str):
        """Return the full ``Datasource`` metadata object for a datasource id."""
        try:
            return self.connect().get_datasource(datasource_id)
        except DatameshError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise DatameshError(f"Could not load metadata for {datasource_id}: {exc}") from exc

    def datasource_summary(self, datasource_id: str) -> dict:
        return self._summarize_datasource(self.datasource(datasource_id))

    # -- staging ----------------------------------------------------------- #
    def _as_query(self, spec):
        """Coerce a spec dict into an oceanum ``Query`` (pass a Query through)."""
        from oceanum.datamesh import Query

        if isinstance(spec, Query):
            return spec
        # Filter to the model's own field names (source of truth), so a field
        # added in a future oceanum release is not silently dropped here.
        allowed = set(Query.model_fields)
        fields = {k: v for k, v in dict(spec).items() if k in allowed and v not in (None, [], {})}
        return Query(**fields)

    def stage(self, spec):
        """Stage a query (metadata only) and return its ``Stage``, or ``None``.

        Uses the Datamesh stage endpoint, which validates the query and reports
        the coordinate keys, container type and size *without* downloading data.
        """
        conn = self.connect()
        query = self._as_query(spec)
        from oceanum.datamesh.session import Session

        session = Session.acquire(conn)
        try:
            return conn._stage_request(query, session)
        except DatameshError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise DatameshError(f"Could not stage this query: {exc}") from exc
        finally:
            try:
                session.close()
            except Exception:  # noqa: BLE001
                logger.debug("Datamesh session close failed", exc_info=True)

    def stage_compatibility(self, spec) -> tuple:
        """Stage a query and report ``(stage, compatible, reason)``.

        ``stage`` is ``None`` when the query returns no data. Compatibility is
        decided from the stage's coordinate keys (needs x/y or a geometry).
        """
        stage = self.stage(spec)
        if stage is None:
            return None, False, "This query returns no data for the current filters."
        compatible, reason = map_compatibility(getattr(stage, "coordkeys", None))
        return stage, compatible, reason

    def _guard_query_size(self, query) -> None:
        """Reject an oversized query up front using the stage size (no download).

        Structural counterpart to the exception/warning text matching below: the
        stage endpoint reports the exact result size before any data is fetched.
        If staging is unavailable we fall back to that post-download guard.
        """
        try:
            stage = self.stage(query)
        except DatameshError:
            return
        size = getattr(stage, "size", 0) if stage is not None else 0
        if size and size > _MAX_RESULT_BYTES:
            raise DatameshError(f"{_TOO_LARGE_MSG} (about {size / 1e9:.1f} GB).")

    # -- query ------------------------------------------------------------- #
    def query(self, spec):
        """Run a query and return the raw container.

        *spec* may be an ``oceanum.datamesh.Query`` (e.g. a saved connection) or
        a spec dict with keys ``datasource`` (required), ``variables``,
        ``timefilter``, ``geofilter``, ``use_dask``. Returns an
        ``xarray.Dataset``, ``geopandas.GeoDataFrame``, ``pandas.DataFrame`` or
        ``None`` (no data).

        A datasource stores longitudes in one frame (0-360 or ±180); a bbox
        crossing that frame's seam (e.g. -10..10 on a 0-360 datasource, or
        170..190 on a ±180 one) cannot be expressed as a single bbox in
        datasource coordinates, so it runs as two queries — one each side of
        the seam — glued back together with longitudes in the frame the user
        asked in.
        """
        parts = self._split_seam_bbox(spec)
        if parts is None:
            return self._query_once(spec)
        results = [(self._query_once(part), offset) for part, offset in parts]
        return self._glue_lon_parts(results)

    def _query_once(self, spec):
        conn = self.connect()
        use_dask = False
        try:
            from oceanum.datamesh import Query as query_cls  # noqa: N813
        except ImportError:  # pragma: no cover - handled by connect()
            query_cls = None
        if query_cls is not None and isinstance(spec, query_cls):
            self._guard_query_size(spec)
            kwargs = {"query": spec}
        else:
            kwargs = {"datasource": spec["datasource"]}
            if spec.get("variables"):
                kwargs["variables"] = list(spec["variables"])
            if spec.get("timefilter"):
                kwargs["timefilter"] = spec["timefilter"]
            if spec.get("geofilter"):
                kwargs["geofilter"] = spec["geofilter"]
            use_dask = bool(spec.get("use_dask", False))
        import warnings

        try:
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                result = conn.query(use_dask=use_dask, **kwargs)
            for w in caught:
                if "too large for direct access" in str(w.message):
                    raise DatameshError(_TOO_LARGE_MSG)
        except DatameshError:
            raise
        except Exception as exc:  # noqa: BLE001
            msg = str(exc)
            if any(tok in msg for tok in ("ZarrClient", "store_like", "too large")):
                raise DatameshError(_TOO_LARGE_MSG) from exc
            raise DatameshError(f"Query failed: {exc}") from exc

        # Guard against materialising a very large lazy result into QGIS.
        nbytes = getattr(result, "nbytes", None)
        if nbytes is not None and nbytes > _MAX_RESULT_BYTES:
            raise DatameshError(f"{_TOO_LARGE_MSG} (result is ~{nbytes / 1e9:.1f} GB).")
        return result

    # -- seam-wrapping bboxes ---------------------------------------------- #
    @staticmethod
    def _bbox_of(spec):
        """The spec's bbox geofilter as ``[x0, y0, x1, y1]``, or None."""
        gf = spec.get("geofilter") if isinstance(spec, dict) else getattr(spec, "geofilter", None)
        if gf is None:
            return None
        gtype = gf.get("type") if isinstance(gf, dict) else getattr(gf, "type", None)
        gtype = getattr(gtype, "value", gtype)
        geom = gf.get("geom") if isinstance(gf, dict) else getattr(gf, "geom", None)
        if str(gtype) != "bbox" or not isinstance(geom, (list, tuple)) or len(geom) != 4:
            return None
        return [float(v) for v in geom]

    @staticmethod
    def _with_bbox(spec, bbox):
        """A copy of *spec* with its geofilter replaced by *bbox*."""
        geofilter = {"type": "bbox", "geom": [float(v) for v in bbox]}
        if isinstance(spec, dict):
            return {**spec, "geofilter": geofilter}
        data = spec.model_dump(exclude_none=True)
        data["geofilter"] = geofilter
        return type(spec)(**data)

    def _lon_frame(self, datasource_id) -> tuple:
        """The datasource's longitude frame: ``(0, 360)`` or ``(-180, 180)``."""
        try:
            bounds = getattr(self.datasource(datasource_id), "bounds", None)
            if bounds is not None and float(bounds[2]) > 180.0:
                return 0.0, 360.0
        except Exception:  # noqa: BLE001 - fall back to the standard frame
            logger.debug("Longitude frame detection failed", exc_info=True)
        return -180.0, 180.0

    def _split_seam_bbox(self, spec):
        """Split a bbox that wraps the datasource's longitude seam.

        Returns ``[(spec_west_of_seam, lon_offset), (spec_east, lon_offset)]``
        where each offset restores that part's longitudes to the frame the
        user asked in, or ``None`` when a single query expresses the bbox.
        """
        bbox = self._bbox_of(spec)
        if bbox is None:
            return None
        x0, y0, x1, y1 = bbox
        if x1 - x0 >= 360.0:  # whole world — the lon constraint is moot
            return None
        # Only fetch the frame when the bbox could disagree with it.
        if not (x0 < 0.0 < x1 or x1 > 180.0 or x0 < -180.0):
            return None
        datasource_id = spec["datasource"] if isinstance(spec, dict) else spec.datasource
        fmin, fmax = self._lon_frame(datasource_id)

        def norm(lon: float) -> float:
            return (lon - fmin) % 360.0 + fmin

        n0, n1 = norm(x0), norm(x1)
        if n0 < n1:  # fits the frame in one piece; the server handles it
            return None
        return [
            (self._with_bbox(spec, [n0, y0, fmax, y1]), x0 - n0),
            (self._with_bbox(spec, [fmin, y0, n1, y1]), x1 - n1),
        ]

    @staticmethod
    def _glue_lon_parts(results):
        """Join the results of a seam-split query back along longitude."""
        import numpy as np

        parts = [(r, off) for r, off in results if r is not None]
        if not parts:
            return None
        first = parts[0][0]
        if not hasattr(first, "dims"):  # tabular / feature results: stack rows
            import pandas as pd

            return pd.concat([r for r, _ in parts]) if len(parts) > 1 else first
        from .converters import _X_NAMES

        xname = next((n for n in _X_NAMES if n in first.coords), None)
        shifted = []
        for r, off in parts:
            if off and xname:
                r = r.assign_coords({xname: r[xname] + off})
            shifted.append(r)
        if xname is None or len(shifted) == 1:
            return shifted[0]
        import xarray as xr

        londim = first[xname].dims[0]
        combined = xr.concat(shifted, dim=londim).sortby(xname)
        lons = np.asarray(combined[xname].values)
        _, unique_idx = np.unique(lons, return_index=True)
        if unique_idx.size != lons.size:  # both halves included the seam column
            combined = combined.isel({londim: np.sort(unique_idx)})
        return combined

    # -- summaries --------------------------------------------------------- #
    @staticmethod
    def _summarize_feature(_id: str, feature) -> dict:
        props = dict(feature.properties or {})
        bounds = None
        geom = getattr(feature, "geometry", None)
        if geom is not None:
            try:
                import shapely.geometry

                bounds = list(shapely.geometry.shape(geom.model_dump()).bounds)
            except Exception:  # noqa: BLE001
                bounds = None
        coordinates = props.get("coordinates") or {}
        return {
            "id": _id,
            "name": props.get("name", _id),
            "description": props.get("description"),
            "tags": props.get("tags") or [],
            "tstart": _iso(props.get("tstart")),
            "tend": _iso(props.get("tend")),
            "bounds": bounds,
            "coordinates": dict(coordinates),
            "driver": props.get("driver"),
        }

    @staticmethod
    def _summarize_datasource(dsrc) -> dict:
        coordinates = {}
        try:
            coordinates = {
                str(getattr(k, "value", k)): v for k, v in (dsrc.coordinates or {}).items()
            }
        except Exception:  # noqa: BLE001
            coordinates = {}
        raw = None
        variables = []
        variable_names = {}
        try:
            raw = dsrc.variables
            variables = list(raw) if raw is not None else []
        except Exception:  # noqa: BLE001
            variables = []
        try:
            # Keyed by the same objects as ``variables`` so lookups match.
            for vid, meta in raw.items() if hasattr(raw, "items") else []:
                attrs = (
                    meta.get("attrs") if isinstance(meta, dict) else getattr(meta, "attrs", None)
                )
                name = _variable_name(attrs)
                if name:
                    variable_names[vid] = name
        except Exception:  # noqa: BLE001 - names are cosmetic; never lose the ids over them
            logger.debug("Variable display-name extraction failed", exc_info=True)
        geometry = None
        try:
            # GeoJSON mapping of the datasource's extent geometry (shapely
            # exposes __geo_interface__); used to outline the extent on the map.
            geometry = getattr(getattr(dsrc, "geometry", None), "__geo_interface__", None)
        except Exception:  # noqa: BLE001
            logger.debug("Datasource geometry extraction failed", exc_info=True)
        return {
            "id": dsrc.id,
            "name": getattr(dsrc, "name", dsrc.id),
            "description": getattr(dsrc, "description", None),
            "tags": list(getattr(dsrc, "tags", None) or []),
            "tstart": _iso(getattr(dsrc, "tstart", None)),
            "tend": _iso(getattr(dsrc, "tend", None)),
            "bounds": list(dsrc.bounds) if getattr(dsrc, "bounds", None) else None,
            "geometry": geometry,
            "coordinates": coordinates,
            "variables": variables,
            "variable_names": variable_names,
            "driver": getattr(dsrc, "driver", None),
            "details": str(getattr(dsrc, "details", "") or "") or None,
        }
