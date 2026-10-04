"""Behaviour tests for the snapshot harness itself.

The harness is what makes the presentation snapshots trustworthy, so its
normalization and comparison semantics are tested directly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.delivery.tui import snapshot_support

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def snapshot_dir(tmp_path, monkeypatch) -> Path:
    monkeypatch.setattr(snapshot_support, "SNAPSHOT_DIR", tmp_path)
    monkeypatch.delenv(snapshot_support.UPDATE_ENV, raising=False)
    return tmp_path


def test_normalize_masks_volatile_runtime_values():
    raw = "marv 0.105.1  session:3f0b917e  RAM 69M/18.0G (9.8G free)"

    normalized = snapshot_support.normalize(raw)

    assert normalized == "marv <version>  session:<id>"
    assert "0.105.1" not in normalized
    assert "3f0b917e" not in normalized


def test_normalize_leaves_stable_text_untouched():
    assert snapshot_support.normalize("✓ Reading  src/parser.py") == "✓ Reading  src/parser.py"


def test_matching_snapshot_passes(snapshot_dir):
    (snapshot_dir / "x.txt").write_text("frame\n", encoding="utf-8")

    snapshot_support.assert_matches_snapshot("x", "frame")


def test_mismatched_snapshot_fails_with_a_diff(snapshot_dir):
    (snapshot_dir / "x.txt").write_text("old frame\n", encoding="utf-8")

    with pytest.raises(AssertionError) as excinfo:
        snapshot_support.assert_matches_snapshot("x", "new frame")

    message = str(excinfo.value)
    assert "old frame" in message
    assert "new frame" in message
    assert snapshot_support.UPDATE_ENV in message


def test_missing_snapshot_fails_instead_of_passing_silently(snapshot_dir):
    with pytest.raises(AssertionError) as excinfo:
        snapshot_support.assert_matches_snapshot("absent", "frame")

    assert "absent.txt" in str(excinfo.value)
    assert snapshot_support.UPDATE_ENV in str(excinfo.value)


def test_update_env_rewrites_the_snapshot(snapshot_dir, monkeypatch):
    monkeypatch.setenv(snapshot_support.UPDATE_ENV, "1")

    snapshot_support.assert_matches_snapshot("x", "fresh frame")

    assert (snapshot_dir / "x.txt").read_text(encoding="utf-8") == "fresh frame\n"


def test_update_env_creates_the_snapshot_directory(tmp_path, monkeypatch):
    nested = tmp_path / "missing" / "snapshots"
    monkeypatch.setattr(snapshot_support, "SNAPSHOT_DIR", nested)
    monkeypatch.setenv(snapshot_support.UPDATE_ENV, "1")

    snapshot_support.assert_matches_snapshot("x", "frame")

    assert (nested / "x.txt").read_text(encoding="utf-8") == "frame\n"
