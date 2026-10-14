"""Single-instance registry and shared-server conflict detection.

marv sessions are independent files, but they share one local model server
(one port, one loaded model). A second TUI that selects a different model will
unload the first one's weights when its first prompt runs. That is not
corruption, but it is silent and expensive, so each running instance records
itself here and a new instance warns when it would evict a live one.

The registry is advisory: a stale file (dead pid, deleted by hand, unreadable)
is ignored, never fatal.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from marv.llm.server_process import process_alive

if TYPE_CHECKING:
    from collections.abc import Mapping


def default_instance_path() -> Path:
    """Where the running instance registers itself."""
    return Path.home() / ".cache" / "marv" / "instance.json"


@dataclass(frozen=True, slots=True)
class InstanceRecord:
    """One running marv process, as seen by a later instance."""

    pid: int
    model: str | None = None
    provider: str | None = None
    session_id: str | None = None
    cwd: str | None = None
    started_at: float = 0.0

    def to_dict(self) -> dict[str, object]:
        """Serializable form."""
        return {
            "pid": self.pid,
            "model": self.model,
            "provider": self.provider,
            "session_id": self.session_id,
            "cwd": self.cwd,
            "started_at": self.started_at,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> InstanceRecord | None:
        """Parse a record, or None when it is missing a usable pid."""
        pid = data.get("pid")
        if not isinstance(pid, int) or pid <= 0:
            return None

        def _optional_str(key: str) -> str | None:
            value = data.get(key)
            return value if isinstance(value, str) and value else None

        started_at = data.get("started_at")
        return cls(
            pid=pid,
            model=_optional_str("model"),
            provider=_optional_str("provider"),
            session_id=_optional_str("session_id"),
            cwd=_optional_str("cwd"),
            started_at=float(started_at) if isinstance(started_at, (int, float)) else 0.0,
        )


def read_record(path: Path) -> InstanceRecord | None:
    """Read the registry file, ignoring corruption."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return InstanceRecord.from_dict(data)


def write_record(path: Path, record: InstanceRecord) -> None:
    """Persist the registry file (best-effort: callers tolerate OSError)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record.to_dict()), encoding="utf-8")


def clear_record(path: Path | None) -> None:
    """Remove the registry file, ignoring a missing file."""
    if path is None:
        return
    try:
        path.unlink()
    except OSError:
        return


def active_other(path: Path, *, pid: int | None = None) -> InstanceRecord | None:
    """The live record for another process, or None when stale/our own."""
    record = read_record(path)
    if record is None:
        return None
    current = os.getpid() if pid is None else pid
    if record.pid == current:
        return None
    return record if process_alive(record.pid) else None


def register(path: Path, *, model: str | None, provider: str | None) -> InstanceRecord:
    """Record this process and return the written entry."""
    record = InstanceRecord(
        pid=os.getpid(),
        model=model,
        provider=provider,
        started_at=time.time(),
    )
    try:
        write_record(path, record)
    except OSError:
        return record
    return record


def conflict_warning(
    *,
    selected_model: str | None,
    other: InstanceRecord | None = None,
    served_model: str | None = None,
) -> str | None:
    """Explain a shared-server collision, or None when there is nothing to say.

    An unrelated instance running the *same* model is fine (both share the
    server). Only a different model means one of them will evict the other.
    """
    if other is not None and other.model and other.model != selected_model:
        return (
            f"another marv session (pid {other.pid}) is using model {other.model}; "
            f"starting {selected_model or 'the selected model'} will restart the shared server"
        )
    if served_model and selected_model and served_model != selected_model:
        return (
            f"model {served_model} is already being served; starting {selected_model} "
            "will restart the shared server"
        )
    return None
