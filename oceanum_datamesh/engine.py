# Copyright 2026 Oceanum / Dave Johnson
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Thin wrapper around the ``oceanum`` Datamesh client.

Kept free of QGIS imports so it can run in a background thread and be tested
standalone. ``oceanum`` itself is imported lazily so the plugin can load and
prompt for installation when the dependency is missing.
"""

from __future__ import annotations

import os
from typing import Optional


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
    def search(
        self, text=None, geofilter=None, timefilter=None, limit=200
    ) -> list[dict]:
        """Return a list of catalog summary dicts matching the filters."""
        cat = self.connect().get_catalog(
            search=text or None, geofilter=geofilter, timefilter=timefilter, limit=limit
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
            raise DatameshError(
                f"Could not load metadata for {datasource_id}: {exc}"
            ) from exc

    def datasource_summary(self, datasource_id: str) -> dict:
        return self._summarize_datasource(self.datasource(datasource_id))

    # -- query ------------------------------------------------------------- #
    def query(self, spec: dict):
        """Run a query described by *spec* and return the raw container.

        *spec* keys: ``datasource`` (required), ``variables``, ``timefilter``,
        ``geofilter``, ``use_dask``. Returns an ``xarray.Dataset``,
        ``geopandas.GeoDataFrame``, ``pandas.DataFrame`` or ``None`` (no data).
        """
        conn = self.connect()
        kwargs = {"datasource": spec["datasource"]}
        if spec.get("variables"):
            kwargs["variables"] = list(spec["variables"])
        if spec.get("timefilter"):
            kwargs["timefilter"] = spec["timefilter"]
        if spec.get("geofilter"):
            kwargs["geofilter"] = spec["geofilter"]
        import warnings

        try:
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                result = conn.query(
                    use_dask=bool(spec.get("use_dask", False)), **kwargs
                )
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
                str(getattr(k, "value", k)): v
                for k, v in (dsrc.coordinates or {}).items()
            }
        except Exception:  # noqa: BLE001
            coordinates = {}
        variables = []
        try:
            variables = list(dsrc.variables)
        except Exception:  # noqa: BLE001
            variables = []
        return {
            "id": dsrc.id,
            "name": getattr(dsrc, "name", dsrc.id),
            "description": getattr(dsrc, "description", None),
            "tags": list(getattr(dsrc, "tags", None) or []),
            "tstart": _iso(getattr(dsrc, "tstart", None)),
            "tend": _iso(getattr(dsrc, "tend", None)),
            "bounds": list(dsrc.bounds) if getattr(dsrc, "bounds", None) else None,
            "coordinates": coordinates,
            "variables": variables,
            "driver": getattr(dsrc, "driver", None),
            "details": str(getattr(dsrc, "details", "") or "") or None,
        }
