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
    dialog._select_area("bbox")
    for key, value in {"xmin": 1.0, "ymin": 2.0, "xmax": 3.0, "ymax": 4.0}.items():
        dialog.bbox_spins[key].setValue(value)
    spec = dialog._build_spec()
    assert spec["datasource"] == "oceanum_wave_glob_era5"
    assert spec["geofilter"] == {"type": "bbox", "geom": [1.0, 2.0, 3.0, 4.0]}
    assert "timefilter" not in spec  # all-time selected


def test_variable_list_shows_names_and_spec_uses_ids():
    dialog = ConnectionDialog(get_iface(), engine=object())
    summary = {
        "id": "ds",
        "name": "DS",
        "variables": ["hs", "tp"],
        "variable_names": {"hs": "Significant wave height"},
    }
    dialog._apply_datasource(summary)
    texts = [dialog.var_list.item(i).text() for i in range(dialog.var_list.count())]
    assert texts == ["Significant wave height (hs)", "tp"]
    dialog.var_list.item(0).setSelected(True)
    assert dialog._build_spec()["variables"] == ["hs"]


def test_apply_query_reselects_variables_by_id():
    from qgis.PyQt.QtCore import Qt

    class _Query:
        variables = ["tp"]
        timefilter = None
        geofilter = None

    dialog = ConnectionDialog(get_iface(), engine=object())
    summary = {
        "id": "ds",
        "name": "DS",
        "variables": ["hs", "tp"],
        "variable_names": {"hs": "Significant wave height", "tp": "Peak period"},
    }
    dialog._apply_datasource(summary, apply_query=_Query())
    selected = [item.data(Qt.ItemDataRole.UserRole) for item in dialog.var_list.selectedItems()]
    assert selected == ["tp"]


def test_bbox_4326_transforms_web_mercator():
    from qgis.core import QgsCoordinateReferenceSystem, QgsRectangle

    from oceanum_datamesh.utils import bbox_4326

    out = bbox_4326(
        QgsRectangle(0.0, 0.0, 10018754.17, 10018754.17),
        QgsCoordinateReferenceSystem("EPSG:3857"),
    )
    assert out[0] == pytest.approx(0.0, abs=1e-6)
    assert out[2] == pytest.approx(90.0, abs=0.01)
    assert out[3] == pytest.approx(66.51, abs=0.05)


def _canvas_4326(iface):
    from qgis.core import QgsCoordinateReferenceSystem

    canvas = iface.mapCanvas()
    canvas.setDestinationCrs(QgsCoordinateReferenceSystem("EPSG:4326"))
    return canvas


def test_bbox_drawn_fills_manual_spins():
    from qgis.core import QgsRectangle

    iface = get_iface()
    _canvas_4326(iface)
    dialog = ConnectionDialog(iface, engine=object())
    dialog._apply_datasource({"id": "ds", "name": "DS", "variables": []})
    dialog._select_area("bbox")
    dialog._on_bbox_drawn(QgsRectangle(1.0, 2.0, 3.0, 4.0))
    values = [dialog.bbox_spins[k].value() for k in ("xmin", "ymin", "xmax", "ymax")]
    assert values == [1.0, 2.0, 3.0, 4.0]
    assert dialog._extent_tool is None  # no dangling tool


def test_bbox_drawn_out_of_range_leaves_spins_untouched():
    from qgis.core import QgsRectangle

    iface = get_iface()
    _canvas_4326(iface)
    dialog = ConnectionDialog(iface, engine=object())
    dialog._apply_datasource({"id": "ds", "name": "DS", "variables": []})
    before = [dialog.bbox_spins[k].value() for k in ("xmin", "ymin", "xmax", "ymax")]
    # Degenerate values the lon/lat spins would clamp must be rejected, not saved.
    dialog._on_bbox_drawn(QgsRectangle(1.0, 2.0, 3.0, 4e6))
    after = [dialog.bbox_spins[k].value() for k in ("xmin", "ymin", "xmax", "ymax")]
    assert after == before


def test_bare_click_cancels_draw_and_restores_preview():
    from qgis.core import QgsRectangle

    iface = get_iface()
    _canvas_4326(iface)
    dialog = ConnectionDialog(iface, engine=object())
    dialog._apply_datasource({"id": "ds", "name": "DS", "variables": []})
    dialog._select_area("bbox")
    assert dialog._bbox_preview is not None
    dialog._draw_bbox_on_map()
    # The dialog must NOT hide: it is shown non-modally, and hiding a dialog
    # inside exec() closes it — the bug this flow replaces. The old outline is
    # cleared so it cannot shadow the new drag.
    assert dialog._bbox_preview is None
    assert dialog._extent_tool is not None
    dialog._on_bbox_drawn(QgsRectangle())  # null extent: click without drag
    assert dialog._extent_tool is None
    assert dialog._prev_map_tool is None
    assert dialog._bbox_preview is not None  # cancelled draw restores the outline


def test_external_tool_switch_ends_draw():
    dialog = ConnectionDialog(get_iface(), engine=object())
    dialog._draw_bbox_on_map()
    assert dialog._extent_tool is not None
    dialog._end_bbox_draw()
    assert dialog._extent_tool is None


def test_closing_dialog_cancels_active_draw():
    dialog = ConnectionDialog(get_iface(), engine=object())
    dialog._draw_bbox_on_map()
    assert dialog._extent_tool is not None
    dialog.reject()
    assert dialog._extent_tool is None
    assert dialog._prev_map_tool is None


def test_extent_draw_tool_visible_band_and_capture():
    from qgis.core import QgsRectangle
    from qgis.PyQt.QtCore import QEvent, QPoint, QPointF, Qt
    from qgis.PyQt.QtGui import QMouseEvent
    from qgis.PyQt.QtWidgets import QApplication

    from oceanum_datamesh.gui.connection_dialog import _ExtentDrawTool

    canvas = _canvas_4326(get_iface())
    canvas.resize(400, 400)
    canvas.setExtent(QgsRectangle(-10.0, -10.0, 10.0, 10.0))
    canvas.show()
    QApplication.processEvents()

    tool = _ExtentDrawTool(canvas)
    captured = []
    tool.extentCaptured.connect(captured.append)
    canvas.setMapTool(tool)

    def mouse(evtype, pos, buttons):
        event = QMouseEvent(
            evtype,
            QPointF(pos),
            canvas.viewport().mapToGlobal(QPointF(pos)),
            Qt.MouseButton.LeftButton,
            buttons,
            Qt.KeyboardModifier.NoModifier,
        )
        QApplication.sendEvent(canvas.viewport(), event)
        QApplication.processEvents()

    mouse(QEvent.Type.MouseButtonPress, QPoint(100, 100), Qt.MouseButton.LeftButton)
    mouse(QEvent.Type.MouseMove, QPoint(300, 300), Qt.MouseButton.LeftButton)
    # High-contrast band is drawn while dragging (the stock tool's grey band
    # was invisible — the bug this tool replaces).
    assert tool._band.numberOfVertices() >= 4
    assert tool._band.strokeColor().alpha() > 0
    mouse(QEvent.Type.MouseButtonRelease, QPoint(300, 300), Qt.MouseButton.NoButton)
    assert len(captured) == 1
    rect = captured[0]
    assert not rect.isEmpty()
    assert rect.xMinimum() < rect.xMaximum()
    # Bare click emits a null rectangle (the cancel path).
    mouse(QEvent.Type.MouseButtonPress, QPoint(50, 50), Qt.MouseButton.LeftButton)
    mouse(QEvent.Type.MouseButtonRelease, QPoint(50, 50), Qt.MouseButton.NoButton)
    assert len(captured) == 2
    assert captured[1].isEmpty()
    canvas.unsetMapTool(tool)
    assert tool._band is None  # band removed from the scene on deactivate


def test_bbox_preview_follows_spins_and_clears():
    from qgis.core import QgsRectangle

    iface = get_iface()
    _canvas_4326(iface)
    dialog = ConnectionDialog(iface, engine=object())
    dialog._apply_datasource({"id": "ds", "name": "DS", "variables": []})
    dialog._select_area("bbox")
    assert dialog._bbox_preview is not None  # bbox mode outlines current spins
    dialog.bbox_spins["xmin"].setValue(1.0)  # hand edits keep it updated
    assert dialog._bbox_preview is not None
    assert dialog._bbox_preview.numberOfVertices() >= 4
    dialog._select_area("full")  # other area modes clear it
    assert dialog._bbox_preview is None
    dialog._select_area("bbox")
    dialog._on_bbox_drawn(QgsRectangle(1.0, 2.0, 3.0, 4.0))  # drawing updates it too
    assert dialog._bbox_preview is not None
    dialog.reject()  # closing the dialog clears it
    assert dialog._bbox_preview is None


def test_open_connection_dialog_survives_draw_and_saves_on_accept():
    from oceanum_datamesh import browser

    dialog = ConnectionDialog(get_iface(), engine=object())
    dialog._result_label, dialog._result_query = "L", object()
    saved = []
    browser._open_connection_dialog(dialog, lambda label, query: saved.append(label))
    assert dialog.isVisible()
    dialog._draw_bbox_on_map()
    assert dialog.isVisible()  # activating the draw must not close the dialog
    dialog._end_bbox_draw()
    dialog.accept()
    assert saved == ["L"]
