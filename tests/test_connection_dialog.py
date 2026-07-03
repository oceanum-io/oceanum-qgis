# Copyright 2026 Oceanum / Dave Johnson
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Tests for the connection dialog's geofilter gating and spec building."""

from __future__ import annotations

import pytest

pytest.importorskip("qgis.core", reason="QGIS Python bindings required")

from qgis.core import QgsFeature, QgsGeometry, QgsPointXY, QgsVectorLayer  # noqa: E402
from qgis.testing import start_app  # noqa: E402

start_app()

from qgis.testing.mocked import get_iface  # noqa: E402

from oceanum_datamesh.gui.connection_dialog import (  # noqa: E402
    ConnectionDialog,
    selected_feature_geofilter,
)


class _Iface:
    def __init__(self, layer=None):
        self._layer = layer

    def activeLayer(self):
        return self._layer

    def mainWindow(self):
        return None


def _layer(wkt_type: str, geom: QgsGeometry) -> QgsVectorLayer:
    layer = QgsVectorLayer(f"{wkt_type}?crs=EPSG:4326", "t", "memory")
    feature = QgsFeature()
    feature.setGeometry(geom)
    layer.dataProvider().addFeatures([feature])
    layer.updateExtents()
    layer.selectAll()
    return layer


def test_point_selection_makes_feature_geofilter():
    layer = _layer("Point", QgsGeometry.fromPointXY(QgsPointXY(10.0, 20.0)))
    geofilter, note = selected_feature_geofilter(_Iface(layer))
    assert geofilter["type"] == "feature"
    assert geofilter["geom"]["type"] == "Feature"
    assert geofilter["geom"]["geometry"]["type"] == "Point"
    assert note == "Point"


def test_polygon_selection_is_allowed():
    ring = [QgsPointXY(0, 0), QgsPointXY(1, 0), QgsPointXY(1, 1), QgsPointXY(0, 0)]
    layer = _layer("Polygon", QgsGeometry.fromPolygonXY([ring]))
    geofilter, note = selected_feature_geofilter(_Iface(layer))
    assert geofilter["geom"]["geometry"]["type"] == "Polygon"
    assert note == "Polygon"


def test_line_selection_is_rejected():
    line = QgsGeometry.fromPolylineXY([QgsPointXY(0, 0), QgsPointXY(1, 1)])
    layer = _layer("LineString", line)
    with pytest.raises(ValueError, match="point, multipoint or single-polygon"):
        selected_feature_geofilter(_Iface(layer))


def test_no_selection_is_rejected():
    layer = QgsVectorLayer("Point?crs=EPSG:4326", "t", "memory")  # nothing selected
    with pytest.raises(ValueError, match="No features selected"):
        selected_feature_geofilter(_Iface(layer))


def test_name_defaults_to_datasource_and_respects_user_edit():
    dialog = ConnectionDialog(get_iface(), engine=object())
    summary = {"id": "ds_a", "name": "Dataset A", "variables": []}
    dialog._apply_datasource(summary)
    assert dialog.name_edit.text() == "Dataset A"
    # Switching datasource replaces an auto-filled name...
    dialog._apply_datasource({"id": "ds_b", "name": "Dataset B", "variables": []})
    assert dialog.name_edit.text() == "Dataset B"
    # ...but never a user-typed one.
    dialog.name_edit.setText("My connection")
    dialog._apply_datasource({"id": "ds_c", "name": "Dataset C", "variables": []})
    assert dialog.name_edit.text() == "My connection"


def test_dialog_builds_manual_bbox_spec():
    dialog = ConnectionDialog(get_iface(), engine=object())
    assert dialog._build_spec() is None  # no datasource chosen yet
    # Simulate a chosen datasource + manual bbox.
    dialog._datasource_id = "oceanum_wave_glob_era5"
    dialog.all_time_cb.setChecked(True)
    dialog._select_area("manual")
    for key, value in {"xmin": 1.0, "ymin": 2.0, "xmax": 3.0, "ymax": 4.0}.items():
        dialog.bbox_spins[key].setValue(value)
    spec = dialog._build_spec()
    assert spec["datasource"] == "oceanum_wave_glob_era5"
    assert spec["geofilter"] == {"type": "bbox", "geom": [1.0, 2.0, 3.0, 4.0]}
    assert "timefilter" not in spec  # all-time selected
