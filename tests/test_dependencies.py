# Copyright 2026 Oceanum / Dave Johnson
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Offline unit tests for runtime dependency installation (no network, no QGIS)."""

from __future__ import annotations

import importlib
import importlib.util
import site
import subprocess
import sys

from oceanum_datamesh import dependencies


def _proc(returncode: int, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def _fake_user_site(tmp_path, monkeypatch, package: str):
    """Point site.getusersitepackages at a tmp dir containing ``package``.

    ``sys.path`` is monkeypatched to a copy so pollution cannot leak between
    tests even when an assertion fails mid-test.
    """
    user_site = tmp_path / "site-packages"
    pkg = user_site / package
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    monkeypatch.setattr(site, "getusersitepackages", lambda: str(user_site))
    monkeypatch.setattr(sys, "path", list(sys.path))
    return user_site


# --------------------------------------------------------------------------- #
# _activate_user_site / oceanum_available
# --------------------------------------------------------------------------- #
def test_activate_makes_new_package_importable_without_restart(tmp_path, monkeypatch):
    """A package dropped into a brand-new user site dir becomes importable."""
    user_site = _fake_user_site(tmp_path, monkeypatch, "_oceanum_qgis_fake_a")
    assert importlib.util.find_spec("_oceanum_qgis_fake_a") is None

    dependencies._activate_user_site()

    assert str(user_site) in sys.path
    assert importlib.util.find_spec("_oceanum_qgis_fake_a") is not None
    importlib.invalidate_caches()  # drop finders for the tmp dir


def test_activate_skips_missing_dir(tmp_path, monkeypatch):
    user_site = tmp_path / "does-not-exist"
    monkeypatch.setattr(site, "getusersitepackages", lambda: str(user_site))
    monkeypatch.setattr(sys, "path", list(sys.path))

    dependencies._activate_user_site()

    assert str(user_site) not in sys.path


def test_activate_survives_site_errors(monkeypatch):
    def boom():
        raise RuntimeError("no user site in this interpreter")

    monkeypatch.setattr(site, "getusersitepackages", boom)
    dependencies._activate_user_site()  # must not raise


def test_available_short_circuits_when_package_present(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(dependencies, "REQUIRED_PACKAGE", "json")
    monkeypatch.setattr(dependencies, "_activate_user_site", lambda: calls.append("activate"))

    assert dependencies.oceanum_available() is True
    assert calls == []


def test_available_activates_user_site_on_miss(tmp_path, monkeypatch):
    """Covers a manual `pip install --user` done in a terminal after QGIS started."""
    _fake_user_site(tmp_path, monkeypatch, "_oceanum_qgis_fake_b")
    monkeypatch.setattr(dependencies, "REQUIRED_PACKAGE", "_oceanum_qgis_fake_b")

    assert dependencies.oceanum_available() is True
    importlib.invalidate_caches()


def test_available_false_when_activation_finds_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(site, "getusersitepackages", lambda: str(tmp_path / "empty"))
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setattr(dependencies, "REQUIRED_PACKAGE", "_oceanum_qgis_absent")

    assert dependencies.oceanum_available() is False


# --------------------------------------------------------------------------- #
# install_oceanum
# --------------------------------------------------------------------------- #
def test_install_success_reports_importability_not_pip_exit(monkeypatch):
    monkeypatch.setattr(dependencies.subprocess, "run", lambda *a, **k: _proc(0))
    monkeypatch.setattr(dependencies, "oceanum_available", lambda: True)
    assert dependencies.install_oceanum()[0] is True

    # pip exited 0 but the package still cannot be imported → not a success
    monkeypatch.setattr(dependencies, "oceanum_available", lambda: False)
    assert dependencies.install_oceanum()[0] is False


def test_install_success_end_to_end_makes_package_importable(tmp_path, monkeypatch):
    """pip 'succeeds' into a fresh user site dir → install_oceanum sees it."""
    _fake_user_site(tmp_path, monkeypatch, "_oceanum_qgis_fake_c")
    monkeypatch.setattr(dependencies, "REQUIRED_PACKAGE", "_oceanum_qgis_fake_c")
    monkeypatch.setattr(dependencies.subprocess, "run", lambda *a, **k: _proc(0))

    ok, _ = dependencies.install_oceanum()

    assert ok is True
    importlib.invalidate_caches()


def test_install_failure_runs_single_attempt(monkeypatch):
    commands: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        commands.append(cmd)
        return _proc(1, stderr="network unreachable")

    monkeypatch.setattr(dependencies.subprocess, "run", fake_run)
    monkeypatch.setattr(dependencies, "oceanum_available", lambda: False)

    ok, output = dependencies.install_oceanum()

    assert ok is False
    assert len(commands) == 1
    assert "network unreachable" in output


def test_install_retries_externally_managed(monkeypatch):
    procs = iter([_proc(1, stderr="error: externally-managed-environment"), _proc(0)])
    commands: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        commands.append(cmd)
        return next(procs)

    monkeypatch.setattr(dependencies.subprocess, "run", fake_run)
    monkeypatch.setattr(dependencies, "oceanum_available", lambda: True)

    ok, _ = dependencies.install_oceanum()

    assert ok is True
    assert len(commands) == 2
    assert "--break-system-packages" in commands[1]


def test_install_subprocess_exception_returns_false(monkeypatch):
    def raise_oserror(*args, **kwargs):
        raise OSError("no such interpreter")

    monkeypatch.setattr(dependencies.subprocess, "run", raise_oserror)

    ok, output = dependencies.install_oceanum()

    assert ok is False
    assert "no such interpreter" in output
