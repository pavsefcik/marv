"""Downloading MLX models from the Hugging Face Hub, with live progress.

marv normally runs models that are already in the local HF hub. When the
remembered (or selected) model is not present, this module fetches it:

* the download runs in a separate Python process — marv itself has no
  ``huggingface_hub`` dependency, so it borrows the interpreter that ships with
  ``mlx-vlm`` (or ``uvx`` on demand);
* that child streams newline-delimited JSON progress events on stdout, and
  ``ModelDownload`` owns the process: it exposes the drained events plus
  cancellation, so the TUI can render progress and the user can abort;
* suggested models for this machine's RAM tier come from the same marv-curator
  catalog the `marv-mlx` runtime uses (read from its cache, so both layers show
  one list), with a bundled fallback so the picker still works offline.
"""

from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import sys
import threading
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Literal, Protocol, runtime_checkable

#: Patterns ``mlx_vlm.utils.get_model_path`` fetches for a model repo.
DEFAULT_ALLOW_PATTERNS = [
    "*.json",
    "*.jsonl",
    "*.safetensors",
    "*.py",
    "*.model",
    "*.tiktoken",
    "*.txt",
    "*.jinja",
]

#: Log format mirroring ``mlx_vlm.utils.get_model_path``'s ``logging.basicConfig``.
_WORKER_LOG_FORMAT = "%(asctime)s - %(levelname)s - %(message)s"

#: Child program. Emits one JSON object per line: a ``plan`` (total bytes), any
#: number of ``progress`` updates, then ``done`` or ``error``.
WORKER_SOURCE = f'''
import argparse, json, logging, sys, time

logging.basicConfig(level=logging.INFO, format="{_WORKER_LOG_FORMAT}")

from huggingface_hub import snapshot_download
from huggingface_hub.utils.tqdm import tqdm as hf_tqdm


def emit(payload):
    sys.stdout.write(json.dumps(payload) + "\\n")
    sys.stdout.flush()


class AggregateProgress(hf_tqdm):
    """Report only the snapshot-wide bar, coalesced to a fixed interval."""

    _last = 0.0
    _interval = 0.2

    def __init__(self, *args, **kwargs):
        self._aggregate = kwargs.pop("name", None) == "huggingface_hub.snapshot_download"
        if self._aggregate:
            kwargs["disable"] = False
        super().__init__(*args, **kwargs)

    def update(self, n=1):
        super().update(n)
        if not self._aggregate:
            return
        now = time.monotonic()
        total = int(self.total or 0)
        done = total > 0 and self.n >= total
        if not done and now - AggregateProgress._last < AggregateProgress._interval:
            return
        AggregateProgress._last = now
        emit({{"event": "progress", "bytes": int(self.n or 0), "total": total}})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--cache-dir", default=None)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--allow", nargs="*", default=None)
    args = parser.parse_args()

    kwargs = {{"allow_patterns": args.allow or None, "cache_dir": args.cache_dir}}
    try:
        plan = snapshot_download(args.repo, dry_run=True, **kwargs)
        total = sum(f.file_size or 0 for f in plan if f.will_download)
        emit({{"event": "plan", "total": int(total), "files": len(plan)}})
        path = snapshot_download(
            args.repo,
            force_download=args.force,
            tqdm_class=AggregateProgress,
            **kwargs,
        )
    except Exception as exc:
        emit({{"event": "error", "message": f"{{type(exc).__name__}}: {{exc}}"}})
        raise SystemExit(1) from exc

    emit({{"event": "done", "path": str(path)}})


main()
'''


class DownloadError(RuntimeError):
    """A model download failed."""


@dataclass(frozen=True, slots=True)
class DownloadProgress:
    """Bytes downloaded for the model currently being fetched."""

    downloaded_bytes: int = 0
    total_bytes: int = 0
    files: int = 0

    @property
    def fraction(self) -> float:
        """Completed fraction in ``[0, 1]``; 0 while the total is unknown."""
        if self.total_bytes <= 0:
            return 0.0
        return min(1.0, self.downloaded_bytes / self.total_bytes)

    def label(self) -> str:
        """Render as ``3.2G / 7.4G (43%)``."""
        if self.total_bytes <= 0:
            return format_size(self.downloaded_bytes) if self.downloaded_bytes else "…"
        return (
            f"{format_size(self.downloaded_bytes)} / "
            f"{format_size(self.total_bytes)} ({int(self.fraction * 100)}%)"
        )


DownloadState = Literal["pending", "running", "done", "failed", "cancelled"]


def format_size(num_bytes: float) -> str:
    """Compact human-readable size, e.g. ``7.4G`` or ``512M``."""
    if num_bytes >= 1024**3:
        return f"{num_bytes / 1024**3:.1f}G"
    if num_bytes >= 1024**2:
        return f"{num_bytes / 1024**2:.0f}M"
    return f"{num_bytes / 1024:.0f}K"


def hub_model_present(hub_dir: Path, model_id: str) -> bool:
    """Whether ``model_id`` has a local snapshot in the HF hub cache."""
    return (hub_dir / f"models--{model_id.replace('/', '--')}" / "snapshots").is_dir()


def is_hub_repo_id(model_id: str) -> bool:
    """Whether ``model_id`` looks like a Hugging Face ``org/name`` repo id.

    Bare names (a local path, or a model id belonging to another provider) are
    not hub repos and must not trigger a download.
    """
    if model_id.startswith(("/", ".", "~")):
        return False
    parts = model_id.split("/")
    return len(parts) == 2 and all(parts)


def resolve_download_interpreter(explicit: str | None = None) -> list[str] | None:
    """Find a Python that can import ``huggingface_hub``, or None.

    Prefers the interpreter bundled with the ``mlx-vlm`` tool (which already
    ships ``huggingface_hub``); falls back to ``uvx --from mlx-vlm``, which
    fetches the package on first use.
    """
    if explicit:
        return [explicit]
    tool_python = Path.home() / ".local" / "share" / "uv" / "tools" / "mlx-vlm" / "bin" / "python"
    if tool_python.is_file() and os.access(tool_python, os.X_OK):
        return [str(tool_python)]
    if uvx := shutil.which("uvx"):
        return [uvx, "--from", "mlx-vlm", "python"]
    if found := shutil.which("python3"):
        return [found]
    return None


def download_log_path(model_id: str, log_dir: Path | None = None) -> Path:
    """Path of the log file a download for ``model_id`` writes to."""
    base = log_dir or Path.home() / ".cache" / "marv" / "logs"
    return base / f"model-download-{model_id.replace('/', '--')}.log"


class _WorkerHandle(Protocol):
    """Minimal subprocess surface used by :class:`ModelDownload`."""

    stdout: IO[str] | None
    returncode: int | None

    def wait(self, timeout: float | None = None) -> int: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...


@dataclass(frozen=True, slots=True)
class DownloadEvent:
    kind: Literal["plan", "progress", "done", "error"]
    downloaded_bytes: int = 0
    total_bytes: int = 0
    files: int = 0
    message: str = ""
    path: str = ""


@dataclass(slots=True)
class ModelDownload:
    """A single model download owned by marv.

    ``start()`` spawns the worker in a background thread; ``poll()`` drains
    whatever progress has arrived (never blocking) and raises
    :class:`DownloadError` once the download has failed. ``run()`` performs the
    whole thing synchronously, which is what tests and headless callers use.
    """

    model_id: str
    hub_dir: Path
    #: Interpreter prefix for the worker; resolved from ``mlx-vlm``/``uvx`` if omitted.
    interpreter: list[str] | None = None
    #: Reuse a cached model instead of re-fetching (set when repairing a snapshot).
    force: bool = False
    log_dir: Path | None = None
    #: Test seam: replace the inline worker program (the repo/cache-dir args are
    #: still appended after this prefix).
    worker_prefix: list[str] | None = None

    state: DownloadState = "pending"
    progress: DownloadProgress = field(default_factory=DownloadProgress)
    error: str | None = None
    local_path: str | None = None

    _events: queue.Queue[DownloadEvent] = field(default_factory=queue.Queue, repr=False)
    _proc: _WorkerHandle | None = field(default=None, repr=False)
    _thread: threading.Thread | None = field(default=None, repr=False)

    # ---- lifecycle ------------------------------------------------------

    def start(self) -> None:
        """Begin the download in a background thread."""
        if self.state != "pending":
            return
        self.state = "running"
        self._thread = threading.Thread(target=self._drive, name="marv-download", daemon=True)
        self._thread.start()

    def run(self) -> None:
        """Perform the download synchronously, raising on failure."""
        if self.state == "pending":
            self.state = "running"
        self._drive()
        self.poll()
        if self.state == "failed":
            raise DownloadError(self.error or "download failed")

    def wait(self, timeout: float | None = None) -> None:
        """Block until the background download finishes (optionally bounded)."""
        if self._thread is not None:
            self._thread.join(timeout)

    @property
    def finished(self) -> bool:
        """Whether the download reached a terminal state."""
        return self.state in ("done", "failed", "cancelled")

    def cancel(self) -> None:
        """Abort the download, stopping the worker process."""
        if self.finished:
            return
        self.state = "cancelled"
        if self._proc is not None:
            self._terminate(self._proc)

    # ---- events ---------------------------------------------------------

    def poll(self) -> list[DownloadEvent]:
        """Drain buffered events, raising once a failure has been seen."""
        events: list[DownloadEvent] = []
        while True:
            try:
                events.append(self._events.get_nowait())
            except queue.Empty:
                break

        for event in events:
            match event.kind:
                case "plan":
                    self.progress = DownloadProgress(
                        total_bytes=event.total_bytes, files=event.files
                    )
                case "progress":
                    self.progress = DownloadProgress(
                        downloaded_bytes=event.downloaded_bytes,
                        total_bytes=event.total_bytes or self.progress.total_bytes,
                        files=self.progress.files,
                    )
                case "done":
                    self.state = "done"
                    self.local_path = event.path or None
                    if self.progress.total_bytes:
                        # Snap the bar to 100%: the worker may have coalesced the
                        # last progress update.
                        self.progress = DownloadProgress(
                            downloaded_bytes=max(
                                self.progress.downloaded_bytes, self.progress.total_bytes
                            ),
                            total_bytes=self.progress.total_bytes,
                            files=self.progress.files,
                        )
                case "error":
                    self.state = "failed"
                    self.error = event.message

        if self.state == "failed" and self.error:
            raise DownloadError(self.error)
        return events

    # ---- worker plumbing ------------------------------------------------

    def _drive(self) -> None:
        if self.worker_prefix is not None:
            prefix = list(self.worker_prefix)
        else:
            interpreter = self.interpreter or resolve_download_interpreter()
            if interpreter is None:
                self.state = "failed"
                self.error = (
                    "no Python with huggingface_hub found; install mlx-vlm "
                    "(`uv tool install mlx-vlm --with jinja2`) to enable model downloads"
                )
                return
            prefix = [*interpreter, "-u", "-c", WORKER_SOURCE]

        argv = [
            *prefix,
            "--repo",
            self.model_id,
            "--allow",
            *DEFAULT_ALLOW_PATTERNS,
            "--cache-dir",
            str(self.hub_dir),
        ]
        if self.force:
            argv.append("--force")

        log_path = download_log_path(self.model_id, self.log_dir)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        returncode = 1
        try:
            with log_path.open("ab") as log:
                proc: _WorkerHandle = subprocess.Popen(
                    argv,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=log,
                    text=True,
                    bufsize=1,
                )
                self._proc = proc
                self._read_stdout(proc.stdout)
                returncode = proc.wait()
        except OSError as exc:
            self.state = "failed"
            self.error = f"could not start the download worker: {exc}"
            return
        finally:
            self._proc = None

        if self.state in ("cancelled", "failed"):
            return
        if returncode != 0:
            self.state = "failed"
            self.error = f"download worker exited with status {returncode}"
            self._append_log_detail()

    def _read_stdout(self, stream: IO[str] | None) -> None:
        if stream is None:
            return
        for line in stream:
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, Mapping):
                self._events.put(_parse_event(payload))

    def _append_log_detail(self) -> None:
        """Attach the tail of the download log to a worker failure message."""
        if not self.error:
            return
        log_path = download_log_path(self.model_id, self.log_dir)
        if not log_path.is_file():
            return
        try:
            lines = log_path.read_text(errors="replace").splitlines()
        except OSError:
            return
        tail = [line for line in lines[-15:] if line.strip()]
        if tail:
            self.error = f"{self.error}\n" + "\n".join(tail)

    def _terminate(self, proc: _WorkerHandle) -> None:
        for stop in (proc.terminate, proc.kill):
            try:
                stop()
                proc.wait(timeout=5)
                return
            except (subprocess.TimeoutExpired, OSError):
                continue


@runtime_checkable
class DownloadHandle(Protocol):
    """Structural view of a download used by delivery (the TUI).

    ``ModelDownload`` satisfies this protocol; tests can supply a lighter double
    without importing the worker machinery.
    """

    model_id: str
    state: DownloadState
    progress: DownloadProgress
    error: str | None
    local_path: str | None

    @property
    def finished(self) -> bool: ...

    def start(self) -> None: ...

    def poll(self) -> list[DownloadEvent]: ...

    def cancel(self) -> None: ...


def _parse_event(payload: Mapping[str, object]) -> DownloadEvent:
    kind = payload.get("event")
    if kind not in ("plan", "progress", "done", "error"):
        return DownloadEvent(kind="progress")
    return DownloadEvent(
        kind=kind,
        downloaded_bytes=_as_int(payload.get("bytes")),
        total_bytes=_as_int(payload.get("total")),
        files=_as_int(payload.get("files")),
        message=str(payload.get("message") or ""),
        path=str(payload.get("path") or ""),
    )


def _as_int(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    return 0


@dataclass(frozen=True, slots=True)
class CuratedModel:
    """One hand-picked model suggestion from the curated list."""

    label: str
    ids: tuple[str, ...]
    tags: str = ""
    description: str = ""

    @property
    def model_id(self) -> str:
        """The id marv runs (the first of a sibling pair)."""
        return self.ids[0]

    @classmethod
    def single(cls, model_id: str, description: str = "") -> CuratedModel:
        """A one-id entry (the common catalog shape)."""
        return cls(label=model_id, ids=(model_id,), description=description)


#: Bundled fallback for the curated list (mirrors the marv-curator model file),
#: grouped by the RAM tier it is intended for. Used when the runtime's live
#: catalog is unavailable, so the picker works on a fresh/offline install.
CURATED_TIERS: dict[int, list[CuratedModel]] = {
    8: [
        CuratedModel.single("mlx-community/Qwen3.5-4B-MLX-4bit", "The compact generalist"),
        CuratedModel.single("mlx-community/gemma-4-e4b-it-4bit", "The quick wit"),
    ],
    16: [
        CuratedModel.single("ornith-ai/Ornith-1.5-9B-MLX-4bit", "The coding sniper"),
        CuratedModel.single("mlx-community/gemma-4-12B-it-qat-OptiQ-4bit", "The creative writer"),
        CuratedModel.single("prism-ml/Ternary-Bonsai-2-27B-mlx-2bit", "The efficiency experiment"),
    ],
    32: [
        CuratedModel.single("bottlecapai/ThinkingCap-Qwen3.8-27B-MLX-4bit-DWQ", "The deep thinker"),
        CuratedModel.single("ornith-ai/Ornith-1.5-35B-A3B-MLX-4bit", "The agentic king"),
        CuratedModel.single("mlx-community/gemma-4-31B-it-qat-OptiQ-4bit", "The creative flagship"),
    ],
}


#: The runtime's cached marv-curator catalog. ``marv-mlx`` refreshes this file on
#: interactive startup, so reading it keeps both download menus on one list.
DEFAULT_CATALOG_PATH = Path.home() / ".cache" / "marv" / "mlx" / "curated-llms.md"


def ram_tier_gb(total_bytes: int | None = None) -> int:
    """The curated tier that matches this machine's installed RAM."""
    if total_bytes is None:
        total_bytes = installed_ram_bytes()
    gigabytes = total_bytes / 1024**3
    if gigabytes >= 32:
        return 32
    if gigabytes >= 16:
        return 16
    return 8


def parse_catalog(text: str) -> dict[int, list[CuratedModel]]:
    """Parse a marv-curator catalog into per-RAM-tier suggestions.

    Mirrors the runtime's ``_marv_mlx_parse_catalog`` so both download menus show
    the same entries: blank-line-separated blocks, ``<n> GB RAM`` tier headers,
    and ``#``-commented entries that stay visible (the marker only highlights a
    hand-pick). Blocks are id-first (``<id>`` then a tagline); the legacy
    title-first and bare-id shapes are accepted too, and a `` & ``-joined id pair
    (Ministral Instruct/Reasoning) is split.
    """
    tiers: dict[int, list[CuratedModel]] = {}
    tier = 0
    block: list[str] = []

    def flush() -> None:
        nonlocal block
        if not block:
            return
        entry = _catalog_entry(block)
        block = []
        if entry is not None:
            tiers.setdefault(tier, []).append(entry)

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            flush()
            continue
        if "GB RAM" in line:
            flush()
            digits = "".join(ch for ch in line if ch.isdigit())
            tier = int(digits) if digits else 0
            continue
        if line.startswith("#"):
            line = line[1:].strip()
            if not line:
                continue
        block.append(line)
    flush()
    return tiers


def _catalog_entry(block: list[str]) -> CuratedModel | None:
    head = block[0]
    second = block[1] if len(block) > 1 else ""
    third = block[2] if len(block) > 2 else ""
    # A legacy leading flag emoji carries no ASCII alphanumerics; peel it off.
    if " " in head:
        first, rest = head.split(" ", 1)
        if not any(ch.isalnum() for ch in first):
            head = rest.strip()
    if not head:
        return None
    if "/" in head:
        ids = _split_ids(head)
        if not ids:
            return None
        # A second line with spaces but no comma is the tagline; otherwise it is
        # a legacy tag token list and the third line is the tagline.
        if second and " " in second and "," not in second:
            tags, description = "", second
        else:
            tags, description = second, third
        return CuratedModel(label=ids[0], ids=ids, tags=tags, description=description)
    if second:
        return CuratedModel(label=head, ids=_split_ids(second), tags=third)
    return CuratedModel(label=head, ids=(head,))


def _split_ids(value: str) -> tuple[str, ...]:
    """Split a ``'a & b'`` id list into its parts (single ids pass through)."""
    return tuple(part for part in (p.strip() for p in value.split(" & ")) if part)


def load_catalog(path: Path | None = None) -> dict[int, list[CuratedModel]]:
    """Read and parse a catalog file; empty when it is absent or unreadable."""
    target = path or DEFAULT_CATALOG_PATH
    try:
        text = target.read_text(encoding="utf-8")
    except OSError:
        return {}
    return parse_catalog(text)


#: Accepted by the startup refresh: long enough for a cold curl of marv-curator,
#: short enough that a hung network cannot hold the TUI open.
REFRESH_TIMEOUT_SECONDS = 30.0


def refresh_catalog_argv(binary: str | None = None) -> list[str] | None:
    """The command that refreshes the runtime's catalog cache, or None.

    This is the runtime's ``curated`` verb: it re-fetches marv-curator and
    rewrites ``~/.cache/marv/mlx/curated-llms.md`` before printing. ``marv``
    reads that file, so running this at startup keeps the download picker from
    showing a list that only the runtime's own TUI would have refreshed.
    """
    resolved = binary or shutil.which("marv-mlx")
    return None if resolved is None else [resolved, "curated"]


class CatalogRefresher:
    """Refreshes the curated catalog once, in the background, per process.

    Both the startup refresh and the first download-modal render can ask for a
    refresh; it runs at most once and later callers :meth:`wait` for the same
    attempt. Failures are remembered, not raised: the picker falls back to the
    cached file and then the bundled list, so a missing runtime or offline
    machine stays silent.
    """

    def __init__(self, binary: str | None = None) -> None:
        self.binary = binary
        self._lock = threading.Lock()
        self._done = threading.Event()
        self._started = False
        self._ok: bool | None = None

    @property
    def finished(self) -> bool:
        return self._done.is_set()

    @property
    def succeeded(self) -> bool | None:
        """True/False once finished, None while pending or never started."""
        return self._ok

    def start(self) -> None:
        """Kick off the refresh; a no-op after the first call."""
        with self._lock:
            if self._started:
                return
            self._started = True
            argv = refresh_catalog_argv(self.binary)
            if argv is None:
                self._finish(False)
                return
            thread = threading.Thread(target=self._refresh, args=(argv,), daemon=True)
            thread.start()

    def wait(self, timeout: float | None = REFRESH_TIMEOUT_SECONDS) -> bool:
        """Refresh (starting it if needed) and block until it is done."""
        self.start()
        self._done.wait(timeout)
        return bool(self._ok)

    def _refresh(self, argv: list[str]) -> None:
        try:
            result = subprocess.run(
                argv,
                check=False,
                capture_output=True,
                text=True,
                timeout=REFRESH_TIMEOUT_SECONDS,
            )
            self._finish(result.returncode == 0)
        except (OSError, subprocess.SubprocessError):
            self._finish(False)

    def _finish(self, ok: bool) -> None:
        self._ok = ok
        self._done.set()


#: Process-wide refresher: one refresh per marv run, shared by the startup kick
#: and the first download-modal render.
_CATALOG_REFRESHER = CatalogRefresher()


def start_catalog_refresh(binary: str | None = None) -> None:
    """Begin a background refresh of the runtime's curated catalog."""
    _CATALOG_REFRESHER.start()


def wait_for_catalog_refresh(timeout: float | None = REFRESH_TIMEOUT_SECONDS) -> bool:
    """Refresh (if not already) and wait for it, for a render that needs it."""
    return _CATALOG_REFRESHER.wait(timeout)


def suggested_models(
    total_bytes: int | None = None, *, catalog_path: Path | None = None
) -> list[CuratedModel]:
    """Hand-picked model suggestions for this machine's RAM tier.

    Prefers the runtime's live catalog (the same list its own download menu
    shows), then falls back to the bundled :data:`CURATED_TIERS` when that file
    is missing or has no entry for this tier.
    """
    tier = ram_tier_gb(total_bytes)
    live = load_catalog(catalog_path).get(tier) or []
    if live:
        return live
    return list(CURATED_TIERS[tier])


def installed_ram_bytes() -> int:
    """Installed physical memory in bytes (0 when it cannot be read)."""
    try:
        result = subprocess.run(
            ["sysctl", "-n", "hw.memsize"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return 0
    out = result.stdout.strip()
    return int(out) if out.isdigit() else 0


def fallback_interpreter() -> list[str]:
    """A last-resort interpreter when ``mlx-vlm`` is not installed."""
    return [sys.executable]
