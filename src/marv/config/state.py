"""Persisted TUI state: last-used selection and per-model latency fits.

The state file lives beside the session directory (``<session_dir>/../state.toml``)
and is the weakest config source: an explicit config file, environment variable,
or CLI flag always wins over it. It exists so a fresh start can preselect
whatever was used last time, and so the wait predictor has this machine's own
measured numbers to work from instead of a hardcoded constant.
"""

from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from marv.llm.latency import LatencyFit

if TYPE_CHECKING:
    from pathlib import Path

STATE_FILE_NAME = "state.toml"


@dataclass(slots=True)
class LastUsedSelection:
    """The provider/model/thinking selection plus learned latency fits."""

    provider: str | None = None
    model: str | None = None
    thinking_level: str | None = None
    #: Per-model prefill/decode calibrations, keyed by model id.
    latency_fits: dict[str, LatencyFit] = field(default_factory=dict)

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

    def fit_for(self, model: str | None) -> LatencyFit:
        """The calibration recorded for ``model`` (empty fit when unknown)."""
        if not model:
            return LatencyFit()
        return self.latency_fits.get(model, LatencyFit())

    def with_fit(self, model: str | None, fit: LatencyFit | None) -> LastUsedSelection:
        """Return a copy carrying ``fit`` for ``model`` (pure)."""
        fits = dict(self.latency_fits)
        if model and fit is not None:
            fits[model] = fit
        return LastUsedSelection(
            provider=self.provider,
            model=self.model,
            thinking_level=self.thinking_level,
            latency_fits=fits,
        )

    def save(self, session_dir: Path) -> None:
        """Persist this exact state beside the session directory.

        Failures are swallowed: remembering state is a convenience and must
        never break a session.
        """
        path = state_path(session_dir)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(render_state(self), encoding="utf-8")
        except OSError:
            return


def render_state(state: LastUsedSelection) -> str:
    """Render state as TOML, scalars first and nested tables last.

    TOML requires every scalar key to appear before any nested table, so the
    ``latency`` tables are written after the selection keys. Model ids contain
    ``/``, which is not a bare key, so every sub-key is JSON-quoted.
    """
    lines = [f"{key} = {json.dumps(value)}" for key, value in state.as_config().items()]
    for model, fit in sorted(state.latency_fits.items()):
        lines.append("")
        lines.append(f"[latency.{json.dumps(model)}]")
        for key, value in fit.as_dict().items():
            lines.append(f"{key} = {_toml_number(value)}")
    return "\n".join(lines) + "\n" if lines else ""


def _toml_number(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return repr(value)
    return str(value)


def state_path(session_dir: Path) -> Path:
    """Return the state file path for a session directory."""
    return session_dir.parent / STATE_FILE_NAME


def load_last_used(session_dir: Path) -> LastUsedSelection:
    """Load the state for a session directory (empty if absent)."""
    return load_last_used_file(state_path(session_dir))


def load_last_used_file(path: Path) -> LastUsedSelection:
    """Load persisted state from an explicit path (empty if unreadable)."""
    try:
        with open(path, "rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return LastUsedSelection()

    return LastUsedSelection(
        provider=_optional_str(data.get("provider")),
        model=_optional_str(data.get("model")),
        thinking_level=_optional_str(data.get("thinking_level")),
        latency_fits=_parse_fits(data.get("latency")),
    )


def _parse_fits(value: object) -> dict[str, LatencyFit]:
    """Parse the ``latency`` tables, dropping entries with no usable samples."""
    if not isinstance(value, dict):
        return {}
    fits: dict[str, LatencyFit] = {}
    for model, fit_data in value.items():
        if not isinstance(model, str) or not model:
            continue
        fit = LatencyFit.from_dict(fit_data)
        if fit.sample_count or fit.cold_samples:
            fits[model] = fit
    return fits


def remember_selection(
    session_dir: Path,
    *,
    provider: str | None,
    model: str | None,
    thinking_level: str | None,
) -> None:
    """Update the selection without discarding latency fits already on disk."""
    state = load_last_used(session_dir)
    state.provider = provider or state.provider
    state.model = model or state.model
    state.thinking_level = thinking_level or state.thinking_level
    state.save(session_dir)


def remember_latency_fit(session_dir: Path, model: str | None, fit: LatencyFit | None) -> None:
    """Merge a new latency fit into the persisted state."""
    if not model or fit is None or not (fit.sample_count or fit.cold_samples):
        return
    state = load_last_used(session_dir).with_fit(model, fit)
    state.save(session_dir)


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None
