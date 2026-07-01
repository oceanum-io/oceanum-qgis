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

_SESSION_DIR: str | None = None


def session_dir() -> str:
    """Return a per-session temp directory for downloaded layer files.

    Files are kept for the life of the QGIS session so loaded layers keep
    working; the OS clears the temp directory afterwards.
    """
    global _SESSION_DIR
    if _SESSION_DIR is None:
        _SESSION_DIR = tempfile.mkdtemp(prefix="oceanum_datamesh_")
    return _SESSION_DIR


def canvas_bbox_4326(iface) -> list[float]:
    """Return the current map canvas extent as ``[xmin, ymin, xmax, ymax]`` in WGS84."""
    canvas = iface.mapCanvas()
    extent = canvas.extent()
    src_crs = canvas.mapSettings().destinationCrs()
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
