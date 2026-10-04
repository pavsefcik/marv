"""Behavior test: the package version is sourced from the VERSION file."""

from __future__ import annotations

from pathlib import Path

import marv


def test_package_version_matches_version_file():
    version_file = Path(__file__).resolve().parents[1] / "VERSION"

    assert version_file.read_text(encoding="utf-8").strip() == marv.__version__
