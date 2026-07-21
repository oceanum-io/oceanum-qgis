# Copyright 2026 Oceanum / Dave Johnson
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""New/Edit dialog for a Datamesh connection (a saved query view of a datasource).

Folds the old search dock into a single flow, modelled on the Datamesh UI:
search the catalog -> pick a datasource -> choose variables, a TimeFilter and a
GeoFilter -> stage the query (validate + compatibility) -> save. The GeoFilter
can be a bounding box (full extent, canvas or manual) or a feature selected on
the map, gated to Point / MultiPoint / single Polygon as Datamesh requires.
"""

from __future__ import annotations

import html
import json

from qgis.core import (
    Qgis,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsFileUtils,
    QgsGeometry,
    QgsProject,
    QgsVectorLayer,
)
from qgis.gui import QgsMapToolExtent
from qgis.PyQt.QtCore import QDateTime, Qt
from qgis.PyQt.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDateTimeEdit,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from ..tasks import FunctionTask, push_message, run_task
from ..utils import bbox_4326, canvas_bbox_4326, to_utc_qdatetime


class ConnectionDialog(QDialog):
    """Collect the parameters of one Datamesh connection (an OceanQL query)."""

    def __init__(self, iface, engine, connection=None, parent=None):
        super().__init__(parent)
        self.iface = iface
        self.engine = engine
        self.connection = connection  # a workspace.Connection when editing
        self._datasource_id = None
        self._summary = None
        self._staged_ok = False
        self._saved_geofilter = None  # geofilter loaded from an edited connection
        self._feature_geofilter = None  # geofilter captured from a map selection
        self._auto_name = None  # name auto-filled from the datasource, if any
        self._result_label = None
        self._result_query = None
        self._extent_tool = None  # live QgsMapToolExtent while drawing a bbox
        self._prev_map_tool = None
        self._tasks: list[FunctionTask] = []

        self.setWindowTitle("Edit Datamesh connection" if connection else "New Datamesh connection")
        self.setMinimumWidth(560)
        self._build_ui()
        self._prefill()

    # ------------------------------------------------------------------ #
    # UI
    # ------------------------------------------------------------------ #
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        # -- name --
        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("Connection name:"))
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("e.g. NW shelf waves — last 7 days")
        self.name_edit.textChanged.connect(self._update_save)
        name_row.addWidget(self.name_edit, 1)
        layout.addLayout(name_row)

        # -- search --
        search_box = QGroupBox("1. Find a datasource")
        search_layout = QVBoxLayout(search_box)
        row = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search the catalog: wave, wind, sea level…")
        self.search_edit.returnPressed.connect(self.run_search)
        self.search_btn = QPushButton("Search")
        self.search_btn.clicked.connect(self.run_search)
        row.addWidget(self.search_edit, 1)
        row.addWidget(self.search_btn)
        search_layout.addLayout(row)
        self.search_extent_cb = QCheckBox("Restrict search to current map canvas extent")
        search_layout.addWidget(self.search_extent_cb)
        self.results_list = QListWidget()
        self.results_list.setMinimumHeight(120)
        self.results_list.currentItemChanged.connect(self._on_result_selected)
        search_layout.addWidget(self.results_list)
        layout.addWidget(search_box)

        # -- filters --
        filt_box = QGroupBox("2. Build the view")
        filt_layout = QVBoxLayout(filt_box)
        self.meta_view = QTextBrowser()
        self.meta_view.setOpenExternalLinks(True)
        self.meta_view.setMaximumHeight(130)
        self.meta_view.setPlaceholderText("Select a datasource from the results above.")
        filt_layout.addWidget(self.meta_view)

        filt_layout.addWidget(QLabel("Variables (none selected = all):"))
        self.var_list = QListWidget()
        self.var_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.var_list.setMaximumHeight(110)
        self.var_list.itemSelectionChanged.connect(self._invalidate_stage)
        filt_layout.addWidget(self.var_list)

        # time
        time_row = QHBoxLayout()
        self.all_time_cb = QCheckBox("All time")
        self.all_time_cb.toggled.connect(self._on_all_time_toggled)
        self.start_edit = QDateTimeEdit()
        self.start_edit.setDisplayFormat("yyyy-MM-dd HH:mm")
        self.start_edit.setCalendarPopup(True)
        self.start_edit.dateTimeChanged.connect(self._invalidate_stage)
        self.end_edit = QDateTimeEdit()
        self.end_edit.setDisplayFormat("yyyy-MM-dd HH:mm")
        self.end_edit.setCalendarPopup(True)
        self.end_edit.dateTimeChanged.connect(self._invalidate_stage)
        time_row.addWidget(QLabel("Time:"))
        time_row.addWidget(self.start_edit)
        time_row.addWidget(QLabel("to"))
        time_row.addWidget(self.end_edit)
        time_row.addWidget(self.all_time_cb)
        filt_layout.addLayout(time_row)

        # area / geofilter
        area_row = QHBoxLayout()
        area_row.addWidget(QLabel("Area:"))
        self.area_combo = QComboBox()
        self.area_combo.addItem("Full dataset extent", "full")
        self.area_combo.addItem("Current canvas extent", "canvas")
        self.area_combo.addItem("Manual bounding box", "manual")
        self.area_combo.addItem("Selected feature(s) on map", "feature")
        self.area_combo.currentIndexChanged.connect(self._on_area_changed)
        area_row.addWidget(self.area_combo, 1)
        filt_layout.addLayout(area_row)

        self.area_hint = QLabel()
        self.area_hint.setWordWrap(True)
        self.area_hint.setVisible(False)
        filt_layout.addWidget(self.area_hint)

        self.bbox_widget = QWidget()
        bbox_grid = QGridLayout(self.bbox_widget)
        bbox_grid.setContentsMargins(0, 0, 0, 0)
        self.bbox_spins = {}
        for i, (key, label, default) in enumerate(
            [
                ("xmin", "Min lon", -180.0),
                ("ymin", "Min lat", -90.0),
                ("xmax", "Max lon", 180.0),
                ("ymax", "Max lat", 90.0),
            ]
        ):
            spin = QDoubleSpinBox()
            spin.setRange(-360.0, 360.0)
            spin.setDecimals(4)
            spin.setValue(default)
            spin.valueChanged.connect(self._invalidate_stage)
            self.bbox_spins[key] = spin
            bbox_grid.addWidget(QLabel(label), i // 2, (i % 2) * 2)
            bbox_grid.addWidget(spin, i // 2, (i % 2) * 2 + 1)
        self.draw_bbox_btn = QPushButton("Draw on map…")
        self.draw_bbox_btn.setToolTip("Drag a rectangle on the map canvas to fill the bounding box")
        self.draw_bbox_btn.clicked.connect(self._draw_bbox_on_map)
        bbox_grid.addWidget(self.draw_bbox_btn, 2, 0, 1, 4)
        self.bbox_widget.setVisible(False)
        filt_layout.addWidget(self.bbox_widget)
        layout.addWidget(filt_box)

        # -- stage / verdict --
        stage_row = QHBoxLayout()
        self.stage_btn = QPushButton("3. Stage && check")
        self.stage_btn.setToolTip(
            "Validate the query against Datamesh and check it can be shown on the map"
        )
        self.stage_btn.clicked.connect(self.run_stage)
        self.stage_btn.setEnabled(False)
        stage_row.addWidget(self.stage_btn)
        self.verdict_label = QLabel()
        self.verdict_label.setWordWrap(True)
        stage_row.addWidget(self.verdict_label, 1)
        layout.addLayout(stage_row)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        # -- buttons --
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.accepted.connect(self._save)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self._update_save()

    # ------------------------------------------------------------------ #
    # Prefill (edit)
    # ------------------------------------------------------------------ #
    def _prefill(self) -> None:
        if self.connection is None:
            return
        from ..workspace import connection_label

        self.name_edit.setText(connection_label(self.connection))
        # Load the datasource metadata, then apply the saved query on top.
        self._load_datasource(self.connection.datasource, apply_query=self.connection.query)

    # ------------------------------------------------------------------ #
    # Search + metadata
    # ------------------------------------------------------------------ #
    def run_search(self) -> None:
        text = self.search_edit.text().strip()
        geofilter = None
        if self.search_extent_cb.isChecked():
            geofilter = {"type": "bbox", "geom": canvas_bbox_4326(self.iface)}
        self.results_list.clear()
        self.search_btn.setEnabled(False)
        self._start_progress()

        def work(_task):
            return self.engine.search(text=text, geofilter=geofilter, limit=200)

        def done(ok, result, error):
            self._end_progress()
            self.search_btn.setEnabled(True)
            if not ok:
                self._warn(f"Search failed: {error}")
                return
            self.results_list.clear()
            for entry in result or []:
                # Show the human name only (the id appears in the metadata pane);
                # fall back to the id when a datasource has no name.
                label = entry.get("name") or entry.get("id")
                item = QListWidgetItem(label)
                item.setToolTip(entry.get("id") or "")
                item.setData(Qt.ItemDataRole.UserRole, entry.get("id"))
                self.results_list.addItem(item)
            if not result:
                self._warn("No datasets matched your search.")

        self._run_task("Datamesh search", work, done)

    def _on_result_selected(self, current, _previous=None) -> None:
        if current is not None:
            self._load_datasource(current.data(Qt.ItemDataRole.UserRole))

    def _load_datasource(self, datasource_id, apply_query=None) -> None:
        self.meta_view.setHtml("<i>Loading metadata…</i>")

        def work(_task):
            return self.engine.datasource_summary(datasource_id)

        def done(ok, result, error):
            if not ok:
                self.meta_view.setHtml(f"<b>Error:</b> {html.escape(str(error))}")
                return
            self._apply_datasource(result, apply_query)

        self._run_task("Datamesh metadata", work, done)

    def _apply_datasource(self, summary: dict, apply_query=None) -> None:
        self._summary = summary
        self._datasource_id = summary.get("id")
        self.meta_view.setHtml(_format_metadata(summary))

        # Geofilters belong to the previous datasource — clear them and drop any
        # "Saved geometry filter" entry so a stale geometry can't leak across.
        self._saved_geofilter = None
        self._feature_geofilter = None
        saved_index = self.area_combo.findData("saved")
        if saved_index >= 0:
            self.area_combo.removeItem(saved_index)

        # Default the connection name to the datasource name, unless the user
        # has typed one themselves (an earlier auto-fill may be replaced).
        current_name = self.name_edit.text().strip()
        if not current_name or current_name == self._auto_name:
            self._auto_name = summary.get("name") or summary.get("id") or ""
            self.name_edit.setText(self._auto_name)

        self.var_list.clear()
        names = summary.get("variable_names") or {}
        for var in summary.get("variables", []) or []:
            name = names.get(var)
            item = QListWidgetItem(f"{name} ({var})" if name else var)
            item.setData(Qt.ItemDataRole.UserRole, var)
            item.setToolTip(var)
            self.var_list.addItem(item)
        self._init_time_range(summary)
        self._init_bbox(summary)

        if apply_query is not None:
            self._apply_query(apply_query)

        self.stage_btn.setEnabled(True)
        self._invalidate_stage()
        self._update_save()

    def _apply_query(self, query) -> None:
        """Overlay a saved query's variables/time/geofilter onto the controls."""
        wanted = set(getattr(query, "variables", None) or [])
        if wanted:
            for i in range(self.var_list.count()):
                item = self.var_list.item(i)
                item.setSelected(item.data(Qt.ItemDataRole.UserRole) in wanted)

        tf = getattr(query, "timefilter", None)
        times = list(getattr(tf, "times", None) or []) if tf else []
        if len(times) == 2 and times[0] and times[1]:
            self.all_time_cb.setChecked(False)
            self.start_edit.setDateTime(to_utc_qdatetime(times[0]))
            self.end_edit.setDateTime(to_utc_qdatetime(times[1]))
        else:
            self.all_time_cb.setChecked(True)

        gf = getattr(query, "geofilter", None)
        if gf is not None:
            geo = gf.model_dump(mode="json") if hasattr(gf, "model_dump") else dict(gf)
            geom = geo.get("geom")
            if geo.get("type") == "bbox" and isinstance(geom, list) and len(geom) == 4:
                self._select_area("manual")
                for key, value in zip(("xmin", "ymin", "xmax", "ymax"), geom):
                    self.bbox_spins[key].setValue(float(value))
            else:  # a feature geofilter — preserve it verbatim in its own slot
                self._saved_geofilter = geo
                if self.area_combo.findData("saved") < 0:
                    self.area_combo.insertItem(0, "Saved geometry filter", "saved")
                self._select_area("saved")

    def _init_time_range(self, summary: dict) -> None:
        start = _time_or_none(summary.get("tstart"))
        end = _time_or_none(summary.get("tend"))
        if start is None and end is None:
            self.all_time_cb.setChecked(True)
            return
        self.all_time_cb.setChecked(False)
        if start is None:
            start = end.addDays(-1)
        if end is None:
            end = QDateTime.currentDateTimeUtc()
        default_end = min(end, start.addDays(1))
        self.start_edit.setDateTimeRange(start, end)
        self.end_edit.setDateTimeRange(start, end)
        self.start_edit.setDateTime(start)
        self.end_edit.setDateTime(default_end)

    def _init_bbox(self, summary: dict) -> None:
        bounds = summary.get("bounds")
        if bounds and len(bounds) == 4:
            for key, value in zip(("xmin", "ymin", "xmax", "ymax"), bounds):
                self.bbox_spins[key].setValue(float(value))

    # ------------------------------------------------------------------ #
    # Geofilter / area
    # ------------------------------------------------------------------ #
    def _select_area(self, key: str) -> None:
        index = self.area_combo.findData(key)
        if index >= 0:
            self.area_combo.setCurrentIndex(index)

    def _on_area_changed(self, _index: int) -> None:
        key = self.area_combo.currentData()
        self.bbox_widget.setVisible(key == "manual")
        self.area_hint.setVisible(False)
        if key == "feature":
            self._capture_feature()
        self._invalidate_stage()

    def _capture_feature(self) -> None:
        try:
            geofilter, note = selected_feature_geofilter(self.iface)
        except ValueError as exc:
            QMessageBox.warning(self, "Selected feature", str(exc))
            self._select_area("full")
            return
        self._feature_geofilter = geofilter
        self.area_hint.setText(f"Using {note} as the geofilter.")
        self.area_hint.setVisible(True)

    def _draw_bbox_on_map(self) -> None:
        """Hide the dialog and let the user drag a bbox on the map canvas."""
        canvas = self.iface.mapCanvas() if self.iface is not None else None
        if canvas is None:
            return
        self._prev_map_tool = canvas.mapTool()
        tool = QgsMapToolExtent(canvas)
        tool.extentChanged.connect(self._on_bbox_drawn)
        tool.deactivated.connect(self._end_bbox_draw)
        self._extent_tool = tool
        canvas.setMapTool(tool)
        self.hide()
        push_message(
            self.iface,
            "Drag a rectangle on the map to set the bounding box.",
            Qgis.Info,
        )

    def _on_bbox_drawn(self, extent) -> None:
        """Fill the spins from the drawn rectangle, then hand the canvas back.

        Runs for a bare click too (a null extent), which just cancels the draw —
        the ``finally`` guarantees the hidden modal dialog always comes back,
        even when the CRS transform fails.
        """
        try:
            if extent is not None and not extent.isEmpty():
                self._fill_bbox_from_extent(extent)
        finally:
            prev, self._prev_map_tool = self._prev_map_tool, None
            tool, self._extent_tool = self._extent_tool, None
            canvas = self.iface.mapCanvas() if self.iface is not None else None
            if canvas is not None and tool is not None:
                try:
                    if prev is not None:
                        canvas.setMapTool(prev)
                    else:
                        canvas.unsetMapTool(tool)
                except Exception:  # noqa: BLE001 - restoring the dialog matters more
                    pass
            self._restore_dialog()

    def _fill_bbox_from_extent(self, extent) -> None:
        crs = self.iface.mapCanvas().mapSettings().destinationCrs()
        bbox = None
        if crs.isValid():
            try:
                bbox = bbox_4326(extent, crs)
            except Exception:  # noqa: BLE001 - e.g. rect outside the CRS domain
                bbox = None
        # Reject values the lon/lat spins would silently clamp (bad canvas CRS
        # or a degenerate transform), rather than saving a garbage bbox.
        if bbox is None or not (
            -360.0 <= bbox[0] <= 360.0
            and -360.0 <= bbox[2] <= 360.0
            and -90.0 <= bbox[1] <= 90.0
            and -90.0 <= bbox[3] <= 90.0
        ):
            push_message(
                self.iface,
                "Could not convert the drawn rectangle to longitude/latitude — "
                "enter the bounding box manually.",
                Qgis.Warning,
            )
            return
        for key, value in zip(("xmin", "ymin", "xmax", "ymax"), bbox):
            self.bbox_spins[key].setValue(float(value))

    def _end_bbox_draw(self) -> None:
        """Restore the dialog when the draw tool is deactivated externally.

        Reached via the tool's ``deactivated`` signal when the user switches
        tools while drawing; the canvas has already installed the new tool, so
        only the dialog state is restored (re-setting the previous tool here
        would stomp the tool the user just picked, reentrantly).
        """
        if self._extent_tool is None:
            return
        self._extent_tool = None
        self._prev_map_tool = None
        self._restore_dialog()

    def _restore_dialog(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()

    def done(self, result: int) -> None:  # noqa: N802 - Qt override
        """A draw session must not outlive the dialog (e.g. plugin teardown)."""
        if self._extent_tool is not None:
            tool, self._extent_tool = self._extent_tool, None
            self._prev_map_tool = None
            canvas = self.iface.mapCanvas() if self.iface is not None else None
            if canvas is not None:
                try:
                    canvas.unsetMapTool(tool)
                except Exception:  # noqa: BLE001 - closing must not fail
                    pass
        super().done(result)

    def _current_geofilter(self):
        key = self.area_combo.currentData()
        if key in ("full", None):
            return None
        if key == "saved":
            return self._saved_geofilter
        if key == "feature":
            return self._feature_geofilter
        if key == "canvas":
            return {"type": "bbox", "geom": canvas_bbox_4326(self.iface)}
        if key == "manual":
            return {
                "type": "bbox",
                "geom": [
                    self.bbox_spins["xmin"].value(),
                    self.bbox_spins["ymin"].value(),
                    self.bbox_spins["xmax"].value(),
                    self.bbox_spins["ymax"].value(),
                ],
            }
        return None

    # ------------------------------------------------------------------ #
    # Staging + save
    # ------------------------------------------------------------------ #
    def _build_spec(self):
        if not self._datasource_id:
            return None
        spec = {"datasource": self._datasource_id}
        variables = [item.data(Qt.ItemDataRole.UserRole) for item in self.var_list.selectedItems()]
        if variables:
            spec["variables"] = variables
        if not self.all_time_cb.isChecked():
            spec["timefilter"] = {
                "type": "range",
                "times": [_edit_utc_iso(self.start_edit), _edit_utc_iso(self.end_edit)],
            }
        geofilter = self._current_geofilter()
        if geofilter:
            spec["geofilter"] = geofilter
        return spec

    def run_stage(self) -> None:
        spec = self._build_spec()
        if not spec:
            return
        self.stage_btn.setEnabled(False)
        self._set_verdict("Staging…", "info")
        self._start_progress()

        def work(_task):
            return self.engine.stage_compatibility(spec)

        def done(ok, result, error):
            self._end_progress()
            self.stage_btn.setEnabled(True)
            if not ok:
                self._staged_ok = False
                self._set_verdict(f"Stage failed: {error}", "error")
                self._update_save()
                return
            stage, compatible, reason = result
            self._staged_ok = bool(compatible)
            self._set_verdict(
                _verdict_text(stage, compatible, reason), "ok" if compatible else "error"
            )
            self._update_save()

        self._run_task("Datamesh stage", work, done)

    def _invalidate_stage(self, *_args) -> None:
        if self._staged_ok:
            self._staged_ok = False
            self._set_verdict("Filters changed — stage again before saving.", "info")
        self._update_save()

    def _update_save(self, *_args) -> None:
        save = self.buttons.button(QDialogButtonBox.StandardButton.Save)
        ready = (
            bool(self._datasource_id) and bool(self.name_edit.text().strip()) and self._staged_ok
        )
        save.setEnabled(ready)

    def _save(self) -> None:
        spec = self._build_spec()
        if not spec:
            return
        label = (
            self.name_edit.text().strip()
            or (self._summary or {}).get("name")
            or self._datasource_id
        )
        try:
            self._result_query = self.engine._as_query(spec)
        except Exception as exc:  # noqa: BLE001
            self._warn(f"Could not build the query: {exc}")
            return
        self._result_label = label
        self.accept()

    def result(self):
        """Return (label, oceanum Query) after the dialog is accepted."""
        return self._result_label, self._result_query

    # ------------------------------------------------------------------ #
    # Small helpers
    # ------------------------------------------------------------------ #
    def _on_all_time_toggled(self, checked: bool) -> None:
        self.start_edit.setEnabled(not checked)
        self.end_edit.setEnabled(not checked)
        self._invalidate_stage()

    def _set_verdict(self, text: str, level: str = "info") -> None:
        colour = {"ok": "#1a7f37", "error": "#b3261e", "info": "#57606a"}.get(level, "#57606a")
        self.verdict_label.setText(f'<span style="color:{colour}">{html.escape(text)}</span>')

    def _run_task(self, description, work, done) -> None:
        run_task(description, work, done, self._tasks)

    def _start_progress(self) -> None:
        self.progress.setRange(0, 0)  # indeterminate (busy) bar
        self.progress.setVisible(True)

    def _end_progress(self) -> None:
        self.progress.setVisible(False)

    def _warn(self, text: str) -> None:
        push_message(self.iface, text, Qgis.Warning)


# --------------------------------------------------------------------------- #
# Module helpers
# --------------------------------------------------------------------------- #
def selected_feature_geofilter(iface):
    """Build a Datamesh ``feature`` geofilter from the active layer's selection.

    Returns ``(geofilter_dict, note)``. Gated to Point / MultiPoint / single
    Polygon (Datamesh's allowed feature geometries); raises ``ValueError`` with
    a user-facing message otherwise.
    """
    layer = iface.activeLayer() if iface is not None else None
    if not isinstance(layer, QgsVectorLayer):
        raise ValueError("Select feature(s) on a vector layer to use as a geofilter.")
    feats = [f for f in layer.selectedFeatures() if f.hasGeometry()]
    if not feats:
        raise ValueError("No features selected. Select a point, multipoint or a single polygon.")
    geoms = [f.geometry() for f in feats]
    gtype = geoms[0].type()
    point_type, polygon_type = _geometry_types()

    if gtype == point_type:
        points = []
        for geom in geoms:
            points.extend(geom.asMultiPoint() if geom.isMultipart() else [geom.asPoint()])
        combined = (
            QgsGeometry.fromMultiPointXY(points)
            if len(points) > 1
            else QgsGeometry.fromPointXY(points[0])
        )
        note = f"MultiPoint ({len(points)} points)" if len(points) > 1 else "Point"
    elif gtype == polygon_type:
        if len(geoms) != 1:
            raise ValueError("Select a single polygon — multiple polygons are not supported.")
        combined = geoms[0]
        if combined.isMultipart():
            parts = combined.asMultiPolygon()
            if len(parts) != 1:
                raise ValueError("Select a single polygon — multipolygons are not supported.")
            combined = QgsGeometry.fromPolygonXY(parts[0])
        note = "Polygon"
    else:
        raise ValueError(
            "Only point, multipoint or single-polygon selections can be used as a "
            "geofilter (lines are not supported)."
        )

    src = layer.crs()
    dst = QgsCoordinateReferenceSystem("EPSG:4326")
    if src.isValid() and src != dst:
        transform = QgsCoordinateTransform(src, dst, QgsProject.instance())
        combined = QgsGeometry(combined)
        combined.transform(transform)

    feature = {"type": "Feature", "geometry": json.loads(combined.asJson()), "properties": {}}
    return {"type": "feature", "geom": feature}, note


def _geometry_types():
    """(point, polygon) geometry-type enums, tolerant of QGIS < 3.30.

    ``Qgis.GeometryType`` exists from 3.30; earlier releases use
    ``QgsWkbTypes.GeometryType``.
    """
    try:
        return Qgis.GeometryType.Point, Qgis.GeometryType.Polygon
    except AttributeError:  # pragma: no cover - only on QGIS < 3.30
        from qgis.core import QgsWkbTypes

        return QgsWkbTypes.PointGeometry, QgsWkbTypes.PolygonGeometry


def _verdict_text(stage, compatible: bool, reason: str) -> str:
    if not compatible:
        return f"✗ Not map-compatible: {reason}"
    container = getattr(getattr(stage, "container", None), "value", None) or "data"
    nbytes = getattr(stage, "size", None)
    size = QgsFileUtils.representFileSize(int(nbytes)) if nbytes else "unknown size"
    kind = "grid → raster" if reason == "x/y" and container == "dataset" else container
    return f"✓ Compatible ({kind}) — about {size}. Ready to save."


def _time_or_none(value):
    """Parse a datasource time hint into a UTC QDateTime, or None if absent."""
    if not value:
        return None
    dt = to_utc_qdatetime(value)
    return dt if dt.isValid() else None


def _edit_utc_iso(edit) -> str:
    """Read a QDateTimeEdit's wall-clock as UTC (the edits display UTC times).

    Reinterprets the displayed value as UTC rather than converting from local,
    so the saved window matches what the user sees regardless of machine zone.
    """
    dt = edit.dateTime()
    dt.setTimeSpec(Qt.TimeSpec.UTC)
    return dt.toString(Qt.DateFormat.ISODate)


def _format_metadata(summary: dict) -> str:
    def esc(value) -> str:
        return html.escape(str(value)) if value is not None else "—"

    bounds = summary.get("bounds")
    bounds_txt = ", ".join(f"{b:.3f}" for b in bounds) if bounds and len(bounds) == 4 else "—"
    variables = summary.get("variables") or []
    var_txt = ", ".join(variables[:20]) + ("…" if len(variables) > 20 else "")
    details = summary.get("details")
    details_html = f'<br><a href="{esc(details)}">Documentation</a>' if details else ""
    return (
        f"<b>{esc(summary.get('name'))}</b><br>"
        f"<code>{esc(summary.get('id'))}</code><br><br>"
        f"{esc(summary.get('description'))}<br><br>"
        f"<b>Time:</b> {esc(summary.get('tstart'))} → {esc(summary.get('tend'))}<br>"
        f"<b>Bounds:</b> {esc(bounds_txt)}<br>"
        f"<b>Variables:</b> {esc(var_txt) if variables else '—'}"
        f"{details_html}"
    )
