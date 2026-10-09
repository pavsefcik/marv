"""Test double: a scripted local model download.

Lets delivery tests drive the download modal without spawning a worker or
touching the Hugging Face Hub. Callers script the progress values that each
``poll`` reports, so a test can assert exactly what the UI shows.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from marv.llm.model_download import DownloadEvent, DownloadProgress

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(slots=True)
class DownloadFake:
    """A ``DownloadHandle`` whose progress the test controls step by step."""

    model_id: str
    #: Progress snapshots consumed one per ``poll`` (the last one repeats).
    snapshots: list[DownloadProgress] = field(default_factory=list)
    #: Terminal state once every snapshot has been consumed.
    final_state: str = "done"
    error: str | None = None
    cancel_result: str = "cancelled"
    hub_dir: Path | None = None

    state: str = "pending"
    progress: DownloadProgress = field(default_factory=DownloadProgress)
    local_path: str | None = None
    started: bool = False
    cancelled: bool = False

    @property
    def finished(self) -> bool:
        return self.state in ("done", "failed", "cancelled")

    def start(self) -> None:
        self.started = True
        self.state = "running"

    def poll(self) -> list[DownloadEvent]:
        if self.state not in ("running", "pending"):
            return []
        if self.snapshots:
            self.progress = self.snapshots.pop(0)
            return [DownloadEvent(kind="progress")]
        self.state = self.final_state
        if self.state == "done" and self.hub_dir is not None:
            snapshot = (
                self.hub_dir / f"models--{self.model_id.replace('/', '--')}" / "snapshots" / "rev"
            )
            snapshot.mkdir(parents=True, exist_ok=True)
            self.local_path = str(snapshot)
        return [DownloadEvent(kind=self.state)]

    def cancel(self) -> None:
        self.cancelled = True
        self.state = self.cancel_result
