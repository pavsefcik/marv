"""Shared test fixtures."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def temp_dir() -> Path:
    """Temporary directory for filesystem-based tests."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture(autouse=True)
def _offline_tui_probes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep TUI startup from touching the network or the real home directory.

    The release probe, the shared-server probe, and the instance registry are
    all patchable seams. Tests that exercise them re-patch within the test.
    """
    from marv.tui.app import AgentApp

    monkeypatch.setattr(AgentApp, "_probe_update", lambda self: None)
    monkeypatch.setattr(AgentApp, "_shared_server_warning", lambda self: None)
    monkeypatch.setattr(AgentApp, "_register_instance", lambda self: None)
