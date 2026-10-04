"""Persisted last-used TUI selection (provider, model, thinking level).

The state file lives beside the session directory (``<session_dir>/../state.toml``)
and is the weakest config source: an explicit config file, environment variable,
or CLI flag always wins over it. It only exists so a fresh start can preselect
whatever was used last time.
"""

from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

STATE_FILE_NAME = "state.toml"


@dataclass(slots=True)
class LastUsedSelection:
    """The provider/model/thinking selection most recently used."""

    provider: str | None = None
    model: str | None = None
    thinking_level: str | None = None

    def as_config(self) -> dict[str, Any]:
        """Render the selection as a config mapping (omitting empty fields)."""
        data: dict[str, Any] = {}
        if self.provider:
            data["provider"] = self.provider
        if self.model:
            data["model"] = self.model
        if self.thinking_level:
            data["thinking_level"] = self.thinking_level
        return data

    def save(self, session_dir: Path) -> None:
        """Persist the selection beside the session directory.

        Failures are swallowed: remembering a selection is a convenience and
        must never break a session.
        """
        path = state_path(session_dir)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            lines = [f"{key} = {json.dumps(value)}" for key, value in self.as_config().items()]
            path.write_text("\n".join(lines) + "\n" if lines else "", encoding="utf-8")
        except OSError:
            return


def state_path(session_dir: Path) -> Path:
    """Return the state file path for a session directory."""
    return session_dir.parent / STATE_FILE_NAME


def load_last_used(session_dir: Path) -> LastUsedSelection:
    """Load the last-used selection for a session directory (empty if absent)."""
    return load_last_used_file(state_path(session_dir))


def load_last_used_file(path: Path) -> LastUsedSelection:
    """Load a last-used selection from an explicit path (empty if unreadable)."""
    try:
        with open(path, "rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return LastUsedSelection()

    return LastUsedSelection(
        provider=_optional_str(data.get("provider")),
        model=_optional_str(data.get("model")),
        thinking_level=_optional_str(data.get("thinking_level")),
    )


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None
