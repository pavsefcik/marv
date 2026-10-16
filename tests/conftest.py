"""Shared test fixtures."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def temp_dir() -> Path:
    """Temporary directory for filesystem-based tests.

    The path is nested one level (``<tmp>/sessions``) so that ``state_path`` —
    ``session_dir.parent / state.toml`` — lands inside this test's temporary
    directory rather than the shared system temp root. Without the nesting,
    every test that passes ``temp_dir`` as a session dir shares one
    ``state.toml`` and persisted themes/latency fits leak between tests.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        sessions = Path(tmpdir) / "sessions"
        sessions.mkdir()
        yield sessions


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
