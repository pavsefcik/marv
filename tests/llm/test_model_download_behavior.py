"""Behavior tests for the model download worker and picker helpers."""

from __future__ import annotations

import sys
import textwrap
from typing import TYPE_CHECKING

import pytest

from marv.llm.model_download import (
    CURATED_TIERS,
    CatalogRefresher,
    DownloadError,
    DownloadProgress,
    ModelDownload,
    format_size,
    hub_model_present,
    load_catalog,
    parse_catalog,
    ram_tier_gb,
    refresh_catalog_argv,
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


def test_suggestions_come_from_the_matching_curated_tier(tmp_path: Path):
    # With no live catalog, the bundled fallback for this tier is used.
    suggestions = suggested_models(16 * 1024**3, catalog_path=tmp_path / "absent.md")

    assert [s.model_id for s in suggestions] == [s.model_id for s in CURATED_TIERS[16]]
    assert all(s.label for s in suggestions)


#: A catalog in the current marv-curator shape: tier headers, id + tagline
#: blocks, a blank-line separator, and a commented-out hand-pick.
CATALOG = """\
8 GB RAM Tier Models

mlx-community/Qwen3.5-4B-MLX-4bit
The compact generalist

16 GB RAM Tier Models

ornith-ai/Ornith-1.5-9B-MLX-4bit
The coding sniper

# mlx-community/gemma-4-12B-it-qat-OptiQ-4bit
The creative writer
"""


def test_parse_catalog_groups_entries_by_ram_tier():
    tiers = parse_catalog(CATALOG)

    assert [e.model_id for e in tiers[8]] == ["mlx-community/Qwen3.5-4B-MLX-4bit"]
    assert [e.model_id for e in tiers[16]] == [
        "ornith-ai/Ornith-1.5-9B-MLX-4bit",
        "mlx-community/gemma-4-12B-it-qat-OptiQ-4bit",
    ]
    assert tiers[16][0].description == "The coding sniper"
    # A commented-out entry stays visible: the '#' only highlights a hand-pick.
    assert tiers[16][1].description == "The creative writer"


def test_parse_catalog_splits_a_ministral_id_pair():
    pair = parse_catalog(
        "16 GB RAM Tier Models\n\n"
        "mlx-community/Ministral-3-8B-Instruct-2512-4bit"
        " & mlx-community/Ministral-3-8B-Reasoning-2512-4bit\n"
        "The instruct/reasoning pair\n"
    )[16][0]

    assert pair.model_id == "mlx-community/Ministral-3-8B-Instruct-2512-4bit"
    assert pair.ids[-1].endswith("Reasoning-2512-4bit")


def test_suggested_models_prefers_the_runtime_catalog(tmp_path: Path):
    catalog = tmp_path / "curated-llms.md"
    catalog.write_text(CATALOG)

    suggestions = suggested_models(16 * 1024**3, catalog_path=catalog)

    assert [s.model_id for s in suggestions] == [
        "ornith-ai/Ornith-1.5-9B-MLX-4bit",
        "mlx-community/gemma-4-12B-it-qat-OptiQ-4bit",
    ]
    assert suggestions[0].description == "The coding sniper"


def test_suggested_models_ignores_a_catalog_without_this_tier(tmp_path: Path):
    catalog = tmp_path / "curated-llms.md"
    catalog.write_text("8 GB RAM Tier Models\n\nmlx-community/Tiny-4bit\nThe small one\n")

    suggestions = suggested_models(32 * 1024**3, catalog_path=catalog)

    assert suggestions == CURATED_TIERS[32]


def test_load_catalog_of_a_missing_file_is_empty(tmp_path: Path):
    assert load_catalog(tmp_path / "absent.md") == {}


def test_refresh_catalog_argv_uses_the_runtime_curated_verb():
    assert refresh_catalog_argv("/usr/local/bin/marv-mlx") == ["/usr/local/bin/marv-mlx", "curated"]
    assert refresh_catalog_argv("true") == ["true", "curated"]


def test_refresh_catalog_argv_is_none_without_a_binary(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda _name: None)

    assert refresh_catalog_argv() is None


def test_catalog_refresher_runs_the_command_once(tmp_path: Path):
    marker = tmp_path / "runs"
    script = tmp_path / "fake-marv-mlx"
    script.write_text(f"#!/bin/sh\necho x >> {marker}\n")
    script.chmod(0o755)
    refresher = CatalogRefresher(str(script))

    refresher.start()
    assert refresher.wait(10) is True
    refresher.start()  # second request reuses the first attempt
    assert refresher.wait(10) is True

    assert marker.read_text().count("x") == 1
    assert refresher.succeeded is True


def test_catalog_refresher_records_a_failure_without_raising(tmp_path: Path):
    script = tmp_path / "failing-marv-mlx"
    script.write_text("#!/bin/sh\nexit 3\n")
    script.chmod(0o755)
    refresher = CatalogRefresher(str(script))

    assert refresher.wait(10) is False

    assert refresher.finished
    assert refresher.succeeded is False


def test_catalog_refresher_without_a_binary_finishes_immediately(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda _name: None)
    refresher = CatalogRefresher()

    assert refresher.wait(10) is False

    assert refresher.finished
