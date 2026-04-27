"""Minimal setup.py shim — install-marker hook for pip install / install -e.

All static metadata lives in pyproject.toml. This shim exists for one
reason only: register custom command classes that drop
~/.swanlake/.install-marker after each `pip install`, so the CLI can
detect when a background agent's editable install has clobbered the
operator's global pointer.

Spec: docs/v0.3.x-worktree-install-isolation-spec.md (T1).

The hooks subclass setuptools' `install` and `develop` commands and
call `swanlake.install_marker.write_marker()` after the parent run().
The marker write is non-fatal — any exception is swallowed so the
underlying install never fails because the marker write failed.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from setuptools import setup
from setuptools.command.develop import develop as _develop
from setuptools.command.install import install as _install

try:
    # Modern setuptools (PEP 660 editables). pip's `pip install -e .`
    # routes through editable_wheel, NOT develop, when the build-backend
    # advertises build_editable. Subclass it so the marker write fires
    # for editable installs too.
    from setuptools.command.editable_wheel import editable_wheel as _editable_wheel
except ImportError:
    _editable_wheel = None  # legacy setuptools — develop subclass is enough


def _write_marker_safely() -> None:
    """Append the source dir to ~/.swanlake/.install-marker after install.

    Failure-tolerant: swallows every exception. The marker is
    informational; an install failure here would be a regression.
    Importing `swanlake.install_marker` directly works because
    setuptools puts the source dir on sys.path before running the
    install command, regardless of whether the install is editable.
    """
    source_root = Path(__file__).resolve().parent
    # Make sure we can import the freshly-installed swanlake. In an
    # editable install this works immediately; in a regular install
    # the package is already in site-packages by the time post-install
    # hooks run.
    sys.path.insert(0, str(source_root))
    try:
        from swanlake import install_marker  # noqa: WPS433 — local import on purpose
    except Exception:  # noqa: BLE001 — never break install on hook failure
        return
    try:
        install_marker.write_marker(source_root)
    except Exception:  # noqa: BLE001
        return


class InstallWithMarker(_install):
    """`pip install .` (or `pip install <wheel>`) writes the marker."""

    def run(self):  # noqa: D401 — setuptools API
        super().run()
        _write_marker_safely()


class DevelopWithMarker(_develop):
    """`pip install -e .` (legacy path) writes the marker.

    Modern pip + setuptools route editable installs through
    `editable_wheel` (PEP 660), not through `develop`. The
    EditableWheelWithMarker subclass below catches those. This
    `develop` subclass remains as a fallback for older toolchains
    that bypass PEP 660.
    """

    def run(self):  # noqa: D401 — setuptools API
        super().run()
        _write_marker_safely()


_cmdclass: dict = {
    "install": InstallWithMarker,
    "develop": DevelopWithMarker,
}

if _editable_wheel is not None:
    class EditableWheelWithMarker(_editable_wheel):
        """`pip install -e .` (PEP 660 path) writes the marker.

        This is the load-bearing case for the worktree-isolation bug
        on modern pip (>= 21.3) + setuptools (>= 64). When pip uses
        the build-backend's `build_editable` hook, it builds an
        editable wheel via this command and never invokes `develop`.

        The marker write happens AFTER the parent run() so the wheel
        is fully built before we touch ~/.swanlake/. If the wheel
        build fails, the marker write is skipped — pip will not have
        installed anything yet.
        """

        def run(self):  # noqa: D401 — setuptools API
            super().run()
            _write_marker_safely()

    _cmdclass["editable_wheel"] = EditableWheelWithMarker


setup(
    cmdclass=_cmdclass,
)
