# Copyright 2026 Oceanum / Dave Johnson
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Offline unit tests for runtime dependency installation (no network, no QGIS)."""

from __future__ import annotations

import importlib.util
import site
import sys
import types

from oceanum_datamesh import dependencies


class FakeProc:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


# --------------------------------------------------------------------------- #
# _activate_user_site
# --------------------------------------------------------------------------- #
def test_activate_adds_freshly_created_user_site_to_sys_path(tmp_path, monkeypatch):
    user_site = tmp_path / "site-packages"
    user_site.mkdir()
    monkeypatch.setattr(site, "getusersitepackages", lambda: str(user_site))
    assert str(user_site) not in sys.path

    dependencies._activate_user_site()
    try:
        assert str(user_site) in sys.path
    finally:
        sys.path.remove(str(user_site))


def test_activate_makes_new_package_importable_without_restart(tmp_path, monkeypatch):
    """End-to-end: a package dropped into a brand-new user site dir is found."""
    user_site = tmp_path / "site-packages"
    pkg = user_site / "_oceanum_qgis_fake_dep"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    monkeypatch.setattr(site, "getusersitepackages", lambda: str(user_site))
    assert importlib.util.find_spec("_oceanum_qgis_fake_dep") is None

    dependencies._activate_user_site()
    try:
        assert importlib.util.find_spec("_oceanum_qgis_fake_dep") is not None
    finally:
        sys.path.remove(str(user_site))
        importlib.invalidate_caches()


def test_activate_skips_missing_dir(tmp_path, monkeypatch):
    user_site = tmp_path / "does-not-exist"
    monkeypatch.setattr(site, "getusersitepackages", lambda: str(user_site))

    dependencies._activate_user_site()

    assert str(user_site) not in sys.path


def test_activate_does_not_duplicate_existing_entry(tmp_path, monkeypatch):
    user_site = tmp_path / "site-packages"
    user_site.mkdir()
    monkeypatch.setattr(site, "getusersitepackages", lambda: str(user_site))
    sys.path.append(str(user_site))

    dependencies._activate_user_site()
    try:
        assert sys.path.count(str(user_site)) == 1
    finally:
        sys.path.remove(str(user_site))


def test_activate_survives_site_errors(monkeypatch):
    def boom():
        raise RuntimeError("no user site in this interpreter")

    monkeypatch.setattr(site, "getusersitepackages", boom)
    dependencies._activate_user_site()  # must not raise


# --------------------------------------------------------------------------- #
# install_oceanum integration with _activate_user_site
# --------------------------------------------------------------------------- #
def test_install_success_activates_user_site(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(dependencies.subprocess, "run", lambda *a, **k: FakeProc(0))
    monkeypatch.setattr(dependencies, "_activate_user_site", lambda: calls.append("activate"))

    ok, _ = dependencies.install_oceanum()

    assert ok is True
    assert calls == ["activate"]


def test_install_failure_activates_before_availability_check(monkeypatch):
    order: list[str] = []
    monkeypatch.setattr(dependencies.subprocess, "run", lambda *a, **k: FakeProc(1, stderr="boom"))
    monkeypatch.setattr(dependencies, "_activate_user_site", lambda: order.append("activate"))
    monkeypatch.setattr(dependencies, "oceanum_available", lambda: order.append("check") or False)

    ok, _ = dependencies.install_oceanum()

    assert ok is False
    assert order == ["activate", "check"]


def test_install_subprocess_exception_does_not_activate(monkeypatch):
    def raise_oserror(*args, **kwargs):
        raise OSError("no such interpreter")

    calls: list[str] = []
    monkeypatch.setattr(dependencies.subprocess, "run", raise_oserror)
    monkeypatch.setattr(dependencies, "_activate_user_site", lambda: calls.append("activate"))

    ok, _ = dependencies.install_oceanum()

    assert ok is False
    assert calls == []


def test_install_retries_externally_managed_then_activates(monkeypatch):
    procs = iter([FakeProc(1, stderr="error: externally-managed-environment"), FakeProc(0)])
    commands: list[list[str]] = []
    calls: list[str] = []

    def fake_run(cmd, **kwargs):
        commands.append(cmd)
        return next(procs)

    monkeypatch.setattr(dependencies.subprocess, "run", fake_run)
    monkeypatch.setattr(dependencies, "_activate_user_site", lambda: calls.append("activate"))

    ok, _ = dependencies.install_oceanum()

    assert ok is True
    assert calls == ["activate"]
    assert "--break-system-packages" in commands[1]


# needed so monkeypatching dependencies.subprocess is meaningful even if the
# module layout changes; guards against subprocess being re-imported locally
def test_subprocess_is_module_attribute():
    assert isinstance(dependencies.subprocess, types.ModuleType)
