# Copyright 2026 Oceanum / Dave Johnson
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Small QGIS helpers used by the GUI (extent transforms, temp storage)."""

from __future__ import annotations

import tempfile

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsProject,
)
from qgis.PyQt.QtCore import QDateTime, Qt

_SESSION_DIR: str | None = None


def to_utc_qdatetime(value) -> QDateTime:
    """Parse a time value into a UTC-spec ``QDateTime``.

    Datamesh times are naive UTC (Python datetimes) or ISO strings; QGIS parses
    zone-less strings as *local* time, so we stamp the result as UTC explicitly.
    This keeps a later ``.toUTC()`` a no-op instead of shifting by the machine's
    offset. An invalid input yields an invalid QDateTime (caller should check).
    """
    if hasattr(value, "strftime"):
        text = value.strftime("%Y-%m-%dT%H:%M:%S")
    else:
        text = str(value)
    dt = QDateTime.fromString(text, Qt.DateFormat.ISODate)
    if dt.isValid() and dt.timeSpec() == Qt.TimeSpec.LocalTime:
        # Zone-less string parsed as local; Datamesh times are UTC, so stamp it
        # (a trailing 'Z' or explicit offset already sets a non-local spec).
        dt.setTimeSpec(Qt.TimeSpec.UTC)
    return dt


def session_dir() -> str:
    """Return a per-session temp directory for downloaded layer files.

    Files are kept for the life of the QGIS session so loaded layers keep
    working; the OS clears the temp directory afterwards.
    """
    global _SESSION_DIR
    if _SESSION_DIR is None:
        _SESSION_DIR = tempfile.mkdtemp(prefix="oceanum_datamesh_")
    return _SESSION_DIR


def bbox_4326(extent, src_crs) -> list[float]:
    """Return ``extent`` (a ``QgsRectangle`` in ``src_crs``) as a WGS84 bbox list."""
    dst_crs = QgsCoordinateReferenceSystem("EPSG:4326")
    if src_crs.isValid() and src_crs != dst_crs:
        transform = QgsCoordinateTransform(src_crs, dst_crs, QgsProject.instance())
        extent = transform.transformBoundingBox(extent)
    return [
        extent.xMinimum(),
        extent.yMinimum(),
        extent.xMaximum(),
        extent.yMaximum(),
    ]


def canvas_bbox_4326(iface) -> list[float]:
    """Return the current map canvas extent as ``[xmin, ymin, xmax, ymax]`` in WGS84."""
    canvas = iface.mapCanvas()
    return bbox_4326(canvas.extent(), canvas.mapSettings().destinationCrs())
