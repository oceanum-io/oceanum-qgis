# Copyright 2026 Oceanum / Dave Johnson
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Detect and install the plugin's Python runtime dependency (``oceanum``).

QGIS ships its own Python, so the ``oceanum`` package (and its dependencies)
must be installed into that interpreter. This module locates the interpreter
and runs ``pip install --user`` on it, retrying with ``--break-system-packages``
on distributions that mark the environment as externally managed (PEP 668).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

#: The package the plugin imports at runtime.
REQUIRED_PACKAGE = "oceanum"


def _activate_user_site() -> None:
    """Make packages installed with ``--user`` importable in this session.

    ``site.py`` adds the user site-packages directory to ``sys.path`` only at
    interpreter startup, and only if the directory already exists at that
    moment. When a pip run creates it, the running QGIS would not see the
    install until a full restart — so add it now. ``addsitedir`` deduplicates
    against ``sys.path`` and processes ``.pth`` files, so it is safe to call
    repeatedly.
    """
    import importlib
    import site

    try:
        user_site = site.getusersitepackages()
        if os.path.isdir(user_site):
            site.addsitedir(user_site)
        importlib.invalidate_caches()
    except Exception:  # noqa: BLE001 - availability checks must never raise
        pass


def oceanum_available() -> bool:
    """Return True if ``oceanum`` can be imported in this interpreter.

    When the package is not found, first activate the user site directory —
    a ``pip install --user`` run since QGIS started (by the plugin's installer
    or by hand in a terminal) may have created a directory the interpreter has
    never scanned — then look again.
    """
    import importlib.util

    if importlib.util.find_spec(REQUIRED_PACKAGE) is not None:
        return True
    _activate_user_site()
    return importlib.util.find_spec(REQUIRED_PACKAGE) is not None


def python_executable() -> str:
    """Best guess at the Python interpreter running QGIS.

    ``sys.executable`` can point at the QGIS binary rather than a Python
    interpreter, so fall back to interpreters under ``sys.prefix`` or on PATH.
    """
    exe = sys.executable or ""
    if exe and os.path.basename(exe).lower().startswith("python"):
        return exe
    for name in ("python3", "python"):
        candidate = os.path.join(sys.prefix, "bin", name)
        if os.path.exists(candidate):
            return candidate
    return shutil.which("python3") or shutil.which("python") or exe or "python3"


def install_command(break_system_packages: bool = False) -> list[str]:
    cmd = [python_executable(), "-m", "pip", "install", "--user", REQUIRED_PACKAGE]
    if break_system_packages:
        cmd.append("--break-system-packages")
    return cmd


def install_oceanum(progress=None) -> tuple[bool, str]:
    """Install ``oceanum`` into the QGIS Python. Returns ``(ok, combined_output)``.

    ``ok`` means the package is importable in this session after the attempt
    (checked via ``oceanum_available``, which also activates a freshly created
    user site directory) — not merely that pip exited 0.

    ``progress`` is an optional callable taking a status string.
    """

    def _emit(msg: str) -> None:
        if progress is not None:
            progress(msg)

    attempts = [install_command(False), install_command(True)]
    output = ""
    for index, cmd in enumerate(attempts):
        _emit(f"Running: {' '.join(cmd)}")
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=1800,
                env={**os.environ, "PIP_DISABLE_PIP_VERSION_CHECK": "1"},
            )
        except Exception as exc:  # noqa: BLE001
            output += f"\n{exc}"
            return False, output
        output += f"\n$ {' '.join(cmd)}\n{proc.stdout}\n{proc.stderr}"
        if proc.returncode == 0:
            break
        # Only retry with --break-system-packages when that is the cause.
        if index == 0 and "externally-managed" not in (proc.stdout + proc.stderr):
            break
    return oceanum_available(), output
