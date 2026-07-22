# Copyright 2026 Oceanum / Dave Johnson
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Dialog for the Datamesh connection settings (token / service / user)."""

from __future__ import annotations

import os

from qgis.core import QgsSettings
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
)

SETTINGS_GROUP = "oceanum_datamesh"
# Not a credential: the dashboard page where users obtain their token.
TOKEN_URL = "https://dashboard.oceanum.io/settings"  # nosec B105


def _get(key: str, env: str, default: str = "") -> str:
    """Read a stored setting, falling back to an environment variable."""
    stored = QgsSettings().value(f"{SETTINGS_GROUP}/{key}", "", type=str)
    return stored or os.environ.get(env, default)


def load_connection_settings() -> dict:
    return {
        "token": _get("token", "DATAMESH_TOKEN"),
        "service": _get("service", "DATAMESH_SERVICE"),
    }


class SettingsDialog(QDialog):
    """Collect and persist the Datamesh connection parameters."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Datamesh connection settings")
        self.setMinimumWidth(460)

        settings = load_connection_settings()

        layout = QVBoxLayout(self)
        intro = QLabel(
            "Set your Datamesh access token. It is stored in QGIS settings for "
            "this profile. If left blank, the DATAMESH_TOKEN environment "
            "variable is used.<br><br>"
            f'Get your token at <a href="{TOKEN_URL}">{TOKEN_URL}</a>.'
        )
        intro.setTextFormat(Qt.TextFormat.RichText)
        intro.setOpenExternalLinks(True)
        intro.setWordWrap(True)
        layout.addWidget(intro)

        form = QFormLayout()
        self.token_edit = QLineEdit(settings["token"])
        self.token_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.token_edit.setPlaceholderText("Datamesh access token")
        self.service_edit = QLineEdit(settings["service"])
        self.service_edit.setPlaceholderText("https://datamesh.oceanum.io (default)")
        form.addRow("Token", self.token_edit)
        form.addRow("Service URL", self.service_edit)
        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def accept(self) -> None:  # noqa: D102
        settings = QgsSettings()
        settings.setValue(f"{SETTINGS_GROUP}/token", self.token_edit.text().strip())
        settings.setValue(f"{SETTINGS_GROUP}/service", self.service_edit.text().strip())
        super().accept()
