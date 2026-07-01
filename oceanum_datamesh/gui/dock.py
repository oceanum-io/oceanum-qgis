# Copyright 2026 Oceanum / Dave Johnson
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""The main dockable panel: search the catalog, filter, and load layers."""

from __future__ import annotations

import html

from qgis.core import (
    Qgis,
    QgsApplication,
    QgsProject,
    QgsRasterLayer,
    QgsVectorLayer,
)
from qgis.PyQt.QtCore import QDateTime, Qt
from qgis.PyQt.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDateTimeEdit,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from ..dependencies import oceanum_available
from ..engine import DatameshEngine
from ..tasks import FunctionTask
from ..utils import canvas_bbox_4326, session_dir
from .settings_dialog import SettingsDialog, load_connection_settings


class DatameshPanel(QWidget):
    """Panel content placed inside the plugin's QDockWidget."""

    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self.engine: DatameshEngine | None = None
        self.results: list[dict] = []
        self.current: dict | None = None
        self._tasks: list[FunctionTask] = []

        self._build_ui()
        self._reload_engine()

    # ------------------------------------------------------------------ #
    # UI construction
    # ------------------------------------------------------------------ #
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(4, 4, 4, 4)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer.addWidget(scroll)

        container = QWidget()
        scroll.setWidget(container)
        layout = QVBoxLayout(container)

        # -- connection banner --
        conn_row = QHBoxLayout()
        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        settings_btn = QPushButton("Settings…")
        settings_btn.clicked.connect(self.open_settings)
        conn_row.addWidget(self.status_label, 1)
        conn_row.addWidget(settings_btn)
        layout.addLayout(conn_row)

        self.install_btn = QPushButton("Install the 'oceanum' Python package")
        self.install_btn.clicked.connect(self.install_dependencies)
        self.install_btn.setVisible(False)
        layout.addWidget(self.install_btn)

        # -- search --
        search_box = QGroupBox("1. Search the catalog")
        search_layout = QVBoxLayout(search_box)
        row = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("e.g. wave hindcast, wind, sea level…")
        self.search_edit.returnPressed.connect(self.run_search)
        self.search_btn = QPushButton("Search")
        self.search_btn.clicked.connect(self.run_search)
        row.addWidget(self.search_edit, 1)
        row.addWidget(self.search_btn)
        search_layout.addLayout(row)
        self.search_extent_cb = QCheckBox("Restrict to current map canvas extent")
        search_layout.addWidget(self.search_extent_cb)
        self.results_list = QListWidget()
        self.results_list.setMinimumHeight(140)
        self.results_list.currentItemChanged.connect(self._on_result_selected)
        search_layout.addWidget(self.results_list)
        layout.addWidget(search_box)

        # -- dataset detail + filters --
        detail_box = QGroupBox("2. Dataset and filters")
        detail_layout = QVBoxLayout(detail_box)
        self.meta_view = QTextBrowser()
        self.meta_view.setOpenExternalLinks(True)
        self.meta_view.setMaximumHeight(150)
        self.meta_view.setPlaceholderText("Select a dataset from the results above.")
        detail_layout.addWidget(self.meta_view)

        detail_layout.addWidget(QLabel("Variables (none selected = all):"))
        self.var_list = QListWidget()
        self.var_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.var_list.setMaximumHeight(120)
        detail_layout.addWidget(self.var_list)

        # time
        time_row = QHBoxLayout()
        self.all_time_cb = QCheckBox("All time")
        self.all_time_cb.toggled.connect(self._on_all_time_toggled)
        self.start_edit = QDateTimeEdit()
        self.start_edit.setDisplayFormat("yyyy-MM-dd HH:mm")
        self.start_edit.setCalendarPopup(True)
        self.end_edit = QDateTimeEdit()
        self.end_edit.setDisplayFormat("yyyy-MM-dd HH:mm")
        self.end_edit.setCalendarPopup(True)
        time_row.addWidget(QLabel("Time:"))
        time_row.addWidget(self.start_edit)
        time_row.addWidget(QLabel("to"))
        time_row.addWidget(self.end_edit)
        time_row.addWidget(self.all_time_cb)
        detail_layout.addLayout(time_row)

        # area
        area_row = QHBoxLayout()
        area_row.addWidget(QLabel("Area:"))
        self.area_combo = QComboBox()
        self.area_combo.addItems(
            ["Full dataset extent", "Current canvas extent", "Manual bounding box"]
        )
        self.area_combo.setCurrentIndex(1)
        self.area_combo.currentIndexChanged.connect(self._on_area_changed)
        area_row.addWidget(self.area_combo, 1)
        detail_layout.addLayout(area_row)

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
            self.bbox_spins[key] = spin
            bbox_grid.addWidget(QLabel(label), i // 2, (i % 2) * 2)
            bbox_grid.addWidget(spin, i // 2, (i % 2) * 2 + 1)
        self.bbox_widget.setVisible(False)
        detail_layout.addWidget(self.bbox_widget)

        layout.addWidget(detail_box)

        # -- load --
        self.load_btn = QPushButton("Load to map")
        self.load_btn.clicked.connect(self.run_load)
        self.load_btn.setEnabled(False)
        layout.addWidget(self.load_btn)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        layout.addStretch(1)

    # ------------------------------------------------------------------ #
    # Connection / dependency state
    # ------------------------------------------------------------------ #
    def _reload_engine(self) -> None:
        settings = load_connection_settings()
        self.engine = DatameshEngine(
            token=settings["token"] or None,
            service=settings["service"] or None,
            user=settings["user"] or None,
        )
        self._refresh_status()

    def _refresh_status(self) -> None:
        if not oceanum_available():
            self.status_label.setText("⚠ The 'oceanum' Python package is not installed.")
            self.install_btn.setVisible(True)
            self._set_enabled(False)
            return
        self.install_btn.setVisible(False)
        if not self.engine or not self.engine.has_token:
            self.status_label.setText("⚠ No Datamesh token. Click Settings… or set DATAMESH_TOKEN.")
            self._set_enabled(False)
            return
        self.status_label.setText("✓ Datamesh ready. Search the catalog below.")
        self._set_enabled(True)

    def _set_enabled(self, ready: bool) -> None:
        self.search_edit.setEnabled(ready)
        self.search_btn.setEnabled(ready)

    # ------------------------------------------------------------------ #
    # Actions
    # ------------------------------------------------------------------ #
    def open_settings(self) -> None:
        dialog = SettingsDialog(self)
        if dialog.exec():
            self._reload_engine()

    def install_dependencies(self) -> None:
        from ..dependencies import install_oceanum

        self.install_btn.setEnabled(False)
        self._start_progress("Installing oceanum…")

        def work(_task):
            return install_oceanum()

        def done(ok, result, error):
            self._end_progress()
            self.install_btn.setEnabled(True)
            if error is not None or not result or not result[0]:
                detail = str(error) if error else (result[1] if result else "")
                self._message(
                    "Could not install oceanum. Install it manually with "
                    "pip install oceanum. See the log for details.",
                    Qgis.Critical,
                )
                from ..tasks import log

                log(detail, Qgis.Warning)
            else:
                self._message("oceanum installed. Ready to use.", Qgis.Success)
            self._refresh_status()

        self._run_task("Install oceanum", work, done)

    def run_search(self) -> None:
        if not self.engine:
            return
        text = self.search_edit.text().strip()
        geofilter = None
        if self.search_extent_cb.isChecked():
            geofilter = {"type": "bbox", "geom": canvas_bbox_4326(self.iface)}
        self.results_list.clear()
        self.search_btn.setEnabled(False)
        self._start_progress("Searching catalog…")

        def work(_task):
            return self.engine.search(text=text, geofilter=geofilter, limit=200)

        def done(ok, result, error):
            self._end_progress()
            self.search_btn.setEnabled(True)
            if not ok:
                self._message(f"Search failed: {error}", Qgis.Critical)
                return
            self._populate_results(result or [])

        self._run_task("Datamesh search", work, done)

    def _populate_results(self, results: list[dict]) -> None:
        self.results = results
        self.results_list.clear()
        for entry in results:
            label = entry.get("name") or entry.get("id")
            item = QListWidgetItem(f"{label}\n{entry.get('id')}")
            item.setData(Qt.ItemDataRole.UserRole, entry.get("id"))
            self.results_list.addItem(item)
        if not results:
            self._message("No datasets matched your search.", Qgis.Info)

    def _on_result_selected(self, current, _previous=None) -> None:
        if current is None or not self.engine:
            return
        self._load_datasource(current.data(Qt.ItemDataRole.UserRole))

    def show_datasource(self, datasource_id: str) -> None:
        """Load a datasource into the filter section (used by the Browser panel)."""
        if self.engine:
            self._load_datasource(datasource_id)

    def _load_datasource(self, datasource_id: str) -> None:
        self.meta_view.setHtml("<i>Loading metadata…</i>")
        self.load_btn.setEnabled(False)

        def work(_task):
            return self.engine.datasource_summary(datasource_id)

        def done(ok, result, error):
            if not ok:
                self.meta_view.setHtml(f"<b>Error:</b> {html.escape(str(error))}")
                return
            self._apply_datasource(result)

        self._run_task("Datamesh metadata", work, done)

    def _apply_datasource(self, summary: dict) -> None:
        self.current = summary
        self.meta_view.setHtml(_format_metadata(summary))

        self.var_list.clear()
        for var in summary.get("variables", []) or []:
            self.var_list.addItem(QListWidgetItem(var))
        if self.var_list.count():
            self.var_list.item(0).setSelected(True)

        self._init_time_range(summary)
        self._init_bbox(summary)
        self.load_btn.setEnabled(True)

    def _init_time_range(self, summary: dict) -> None:
        start = _parse_iso(summary.get("tstart"))
        end = _parse_iso(summary.get("tend"))
        if start is None and end is None:
            self.all_time_cb.setChecked(True)
            return
        self.all_time_cb.setChecked(False)
        if start is None:
            start = end.addDays(-1)
        if end is None:
            end = QDateTime.currentDateTimeUtc()
        # Default to a small one-day window anchored at the start of coverage.
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
    # Load
    # ------------------------------------------------------------------ #
    def run_load(self) -> None:
        if not self.engine or not self.current:
            return
        spec = self._build_query_spec()
        name = self.current.get("name") or self.current["id"]
        coordinates = self.current.get("coordinates") or {}
        self.load_btn.setEnabled(False)
        self._start_progress("Querying Datamesh…")

        def work(_task):
            from .. import converters

            result = self.engine.query(spec)
            if result is None:
                raise RuntimeError("No data found for this query. Try a different time or area.")
            return converters.result_to_layers(
                result,
                session_dir(),
                name,
                coordinates=coordinates,
                variables=spec.get("variables"),
            )

        def done(ok, result, error):
            self._end_progress()
            self.load_btn.setEnabled(True)
            if not ok:
                self._message(str(error), Qgis.Warning)
                return
            self._add_layers(result or [])

        self._run_task("Datamesh query", work, done)

    def _build_query_spec(self) -> dict:
        spec: dict = {"datasource": self.current["id"]}

        selected_vars = [item.text() for item in self.var_list.selectedItems()]
        if selected_vars:
            spec["variables"] = selected_vars

        if not self.all_time_cb.isChecked():
            start = self.start_edit.dateTime().toUTC().toString(Qt.DateFormat.ISODate)
            end = self.end_edit.dateTime().toUTC().toString(Qt.DateFormat.ISODate)
            spec["timefilter"] = {"type": "range", "times": [start, end]}

        area = self.area_combo.currentIndex()
        if area == 1:  # canvas extent
            spec["geofilter"] = {"type": "bbox", "geom": canvas_bbox_4326(self.iface)}
        elif area == 2:  # manual bbox
            spec["geofilter"] = {
                "type": "bbox",
                "geom": [
                    self.bbox_spins["xmin"].value(),
                    self.bbox_spins["ymin"].value(),
                    self.bbox_spins["xmax"].value(),
                    self.bbox_spins["ymax"].value(),
                ],
            }
        # area == 0 -> full extent: no geofilter
        return spec

    def _add_layers(self, specs: list) -> None:
        project = QgsProject.instance()
        added = 0
        for spec in specs:
            layer = self._layer_from_spec(spec)
            if layer is not None and layer.isValid():
                project.addMapLayer(layer)
                added += 1
            else:
                self._message(f"Could not load layer: {spec.name}", Qgis.Warning)
        if added:
            self._message(f"Loaded {added} layer(s) from Datamesh.", Qgis.Success)

    @staticmethod
    def _layer_from_spec(spec):
        if spec.kind == "raster":
            return QgsRasterLayer(spec.path, spec.name)
        uri = spec.path
        if spec.sublayer:
            uri = f"{spec.path}|layername={spec.sublayer}"
        return QgsVectorLayer(uri, spec.name, "ogr")

    # ------------------------------------------------------------------ #
    # Small helpers
    # ------------------------------------------------------------------ #
    def _on_all_time_toggled(self, checked: bool) -> None:
        self.start_edit.setEnabled(not checked)
        self.end_edit.setEnabled(not checked)

    def _on_area_changed(self, index: int) -> None:
        self.bbox_widget.setVisible(index == 2)

    def _run_task(self, description, work, done) -> None:
        task = FunctionTask(description, work, done)
        self._tasks.append(task)

        def cleanup(*_):
            if task in self._tasks:
                self._tasks.remove(task)

        task.taskCompleted.connect(cleanup)
        task.taskTerminated.connect(cleanup)
        QgsApplication.taskManager().addTask(task)

    def _start_progress(self, message: str) -> None:
        self.progress.setRange(0, 0)  # indeterminate (busy) bar
        self.progress.setVisible(True)
        self.iface.mainWindow().statusBar().showMessage(message, 4000)

    def _end_progress(self) -> None:
        self.progress.setVisible(False)

    def _message(self, text: str, level=Qgis.Info) -> None:
        self.iface.messageBar().pushMessage("Oceanum Datamesh", text, level=level)


# --------------------------------------------------------------------------- #
# Formatting helpers
# --------------------------------------------------------------------------- #
def _parse_iso(value):
    if not value:
        return None
    dt = QDateTime.fromString(str(value), Qt.DateFormat.ISODate)
    return dt if dt.isValid() else None


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
