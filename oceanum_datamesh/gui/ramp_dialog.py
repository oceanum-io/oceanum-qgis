# Copyright 2026 Oceanum / Dave Johnson
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Small dialog choosing a colour ramp and value range for a raster group."""

from __future__ import annotations

from qgis.gui import QgsColorRampButton
from qgis.PyQt.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QVBoxLayout,
)


class GroupRampDialog(QDialog):
    """Pick a colour ramp plus the min/max it maps, for a whole group."""

    def __init__(self, ramp=None, vmin: float = 0.0, vmax: float = 1.0, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Colour ramp for group")
        self.setMinimumWidth(360)

        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.ramp_button = QgsColorRampButton()
        self.ramp_button.setColorRampDialogTitle("Colour ramp")
        if ramp is not None:
            self.ramp_button.setColorRamp(ramp)
        form.addRow("Colour ramp", self.ramp_button)

        self.min_spin = QDoubleSpinBox()
        self.max_spin = QDoubleSpinBox()
        for spin, value in ((self.min_spin, vmin), (self.max_spin, vmax)):
            spin.setRange(-1e12, 1e12)
            spin.setDecimals(6)
            spin.setValue(float(value))
            spin.valueChanged.connect(self._update_ok)
        form.addRow("Minimum", self.min_spin)
        form.addRow("Maximum", self.max_spin)
        layout.addLayout(form)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self._update_ok()

    def _update_ok(self, *_args) -> None:
        ok = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        ok.setEnabled(self.min_spin.value() < self.max_spin.value())

    def ramp(self):
        ramp = self.ramp_button.colorRamp()
        return ramp.clone() if ramp is not None else None

    def value_range(self) -> tuple:
        return self.min_spin.value(), self.max_spin.value()
