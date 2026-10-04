"""Behavior tests for the model download worker and picker helpers."""

from __future__ import annotations

import sys
import textwrap
from typing import TYPE_CHECKING

import pytest

from marv.llm.model_download import (
    CURATED_TIERS,
    DownloadError,
    DownloadProgress,
    ModelDownload,
    format_size,
    hub_model_present,
    ram_tier_gb,
    resolve_download_interpreter,
    suggested_models,
)

if TYPE_CHECKING:
    from pathlib import Path

#: Stand-in for the inline HF worker: streams a plan, two progress updates, done.
FAKE_WORKER = """\
import json, sys

sys.stdout.write(json.dumps({"event": "plan", "total": 1000, "files": 3}) + "\\n")
sys.stdout.write(json.dumps({"event": "progress", "bytes": 250, "total": 1000}) + "\\n")
sys.stdout.write(json.dumps({"event": "progress", "bytes": 750, "total": 1000}) + "\\n")
sys.stdout.write(json.dumps({"event": "done", "path": "/hub/snapshot"}) + "\\n")
"""

FAILING_WORKER = """\
import json, sys
sys.stdout.write(json.dumps({"event": "error", "message": "boom: gated repo"}) + "\\n")
raise SystemExit(1)
"""


def write_worker(tmp_path: Path, source: str) -> Path:
    path = tmp_path / "worker.py"
    path.write_text(textwrap.dedent(source))
    return path


def make_download(tmp_path: Path, source: str, **kwargs: object) -> ModelDownload:
    worker = write_worker(tmp_path, source)
    return ModelDownload(
        model_id="org/Model",
        hub_dir=tmp_path / "hub",
        worker_prefix=[sys.executable, str(worker)],
        log_dir=tmp_path,
        **kwargs,  # type: ignore[arg-type]
    )


def test_run_reports_final_progress_and_local_path(tmp_path: Path):
    download = make_download(tmp_path, FAKE_WORKER)

    download.run()

    assert download.state == "done"
    assert download.local_path == "/hub/snapshot"
    assert download.progress.downloaded_bytes == 1000
    assert download.progress.total_bytes == 1000


def test_poll_drains_events_in_order_and_tracks_bytes(tmp_path: Path):
    download = make_download(tmp_path, FAKE_WORKER)
    download.start()
    download.wait()

    download.poll()
    assert download.state == "done"
    assert download.progress.total_bytes == 1000
    assert download.progress.files == 3
    # The terminal event snaps a coalesced bar to 100%.
    assert download.progress.downloaded_bytes == 1000


def test_a_worker_error_raises_with_the_reported_message(tmp_path: Path):
    download = make_download(tmp_path, FAILING_WORKER)
    download.start()
    download.wait()

    with pytest.raises(DownloadError, match="boom: gated repo"):
        download.poll()
    assert download.state == "failed"


def test_run_raises_when_the_worker_reports_an_error(tmp_path: Path):
    download = make_download(tmp_path, FAILING_WORKER)

    with pytest.raises(DownloadError, match="boom: gated repo"):
        download.run()


def test_a_nonzero_exit_without_an_event_is_a_failure(tmp_path: Path):
    download = make_download(tmp_path, "raise SystemExit(3)")

    with pytest.raises(DownloadError, match="status 3"):
        download.run()


def test_cancel_stops_the_download(tmp_path: Path):
    download = make_download(tmp_path, "import time\nwhile True: time.sleep(0.01)")
    download.start()
    download.cancel()
    download.wait(5)

    assert download.state == "cancelled"
    assert download.finished


def test_missing_interpreter_fails_with_actionable_message(tmp_path: Path):
    download = ModelDownload(
        model_id="org/Model",
        hub_dir=tmp_path / "hub",
        interpreter=["/nonexistent/python"],
        log_dir=tmp_path,
    )

    with pytest.raises(DownloadError, match="could not start the download worker"):
        download.run()


def test_progress_label_and_fraction():
    progress = DownloadProgress(downloaded_bytes=3 * 1024**3, total_bytes=6 * 1024**3)

    assert progress.fraction == pytest.approx(0.5)
    assert progress.label() == "3.0G / 6.0G (50%)"


def test_progress_without_a_total_reports_bytes_only():
    assert DownloadProgress(downloaded_bytes=512 * 1024**2).label() == "512M"
    assert DownloadProgress().label() == "…"
    assert DownloadProgress().fraction == 0.0


def test_format_size():
    assert format_size(0) == "0K"
    assert format_size(2048) == "2K"
    assert format_size(5 * 1024**2) == "5M"
    assert format_size(int(1.5 * 1024**3)) == "1.5G"


def test_hub_model_present_requires_a_snapshot_directory(tmp_path: Path):
    assert hub_model_present(tmp_path, "org/Model") is False
    (tmp_path / "models--org--Model" / "snapshots" / "rev").mkdir(parents=True)

    assert hub_model_present(tmp_path, "org/Model") is True


def test_resolve_download_interpreter_prefers_mlx_vlm_tool_python(tmp_path: Path, monkeypatch):
    tool_python = tmp_path / ".local" / "share" / "uv" / "tools" / "mlx-vlm" / "bin" / "python"
    tool_python.parent.mkdir(parents=True)
    tool_python.write_text("#!/bin/sh\n")
    tool_python.chmod(0o755)
    monkeypatch.setenv("HOME", str(tmp_path))

    assert resolve_download_interpreter() == [str(tool_python)]


def test_resolve_download_interpreter_accepts_an_explicit_override():
    assert resolve_download_interpreter("/opt/python") == ["/opt/python"]


def test_ram_tier_selects_the_machine_tier():
    assert ram_tier_gb(8 * 1024**3) == 8
    assert ram_tier_gb(16 * 1024**3) == 16
    assert ram_tier_gb(24 * 1024**3) == 16
    assert ram_tier_gb(64 * 1024**3) == 32


def test_suggestions_come_from_the_matching_curated_tier():
    suggestions = suggested_models(16 * 1024**3)

    assert [s.model_id for s in suggestions] == [s.model_id for s in CURATED_TIERS[16]]
    assert all(s.label for s in suggestions)


def test_curated_ministral_pairs_run_the_instruct_half():
    pair = next(entry for tier in CURATED_TIERS.values() for entry in tier if len(entry.ids) == 2)

    assert pair.model_id == pair.ids[0]
    assert "Instruct" in pair.model_id
    assert "Reasoning" in pair.ids[1]
