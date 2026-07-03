# Copyright 2026 Oceanum / Dave Johnson
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Settings dialog: token link, and auto-open when no token is configured."""

from __future__ import annotations

import pytest

pytest.importorskip("qgis.core", reason="QGIS Python bindings required")

from qgis.PyQt.QtWidgets import QLabel  # noqa: E402
from qgis.testing import start_app  # noqa: E402

start_app()

from oceanum_datamesh import browser  # noqa: E402
from oceanum_datamesh.gui.settings_dialog import TOKEN_URL, SettingsDialog  # noqa: E402


def test_settings_dialog_shows_token_link():
    dialog = SettingsDialog()
    labels = [w.text() for w in dialog.findChildren(QLabel)]
    intro = next(t for t in labels if "access token" in t)
    assert TOKEN_URL in intro
    assert '<a href="' in intro  # rendered as a clickable link
    # The intro label opens the link in an external browser.
    link_label = next(w for w in dialog.findChildren(QLabel) if "access token" in w.text())
    assert link_label.openExternalLinks()


def test_new_connection_opens_settings_when_no_token(monkeypatch):
    opened = []
    monkeypatch.setattr(browser, "oceanum_available", lambda: True)

    class _NoToken:
        has_token = False

    monkeypatch.setattr(browser, "_engine", lambda: _NoToken())
    monkeypatch.setattr(
        browser, "open_settings", lambda parent=None: opened.append(parent) or False
    )
    # Trying to add a connection without a token must pop the settings dialog.
    browser.new_connection(parent=None)
    assert opened == [None]
