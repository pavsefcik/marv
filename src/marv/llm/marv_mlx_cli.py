"""Thin client for the external ``marv-mlx`` runtime CLI (Seam B).

``marv-mlx`` is the recommended local-LLM runtime that sits underneath the
harness. By default marv manages ``mlx_vlm.server`` itself (the *embedded*
server manager); when configured with ``server_manager = "marv-mlx"`` it
delegates model lifecycle to the runtime instead.

The runtime CLI contract this client relies on: data on stdout, logs on
stderr, ``--json`` where noted, and a stable exit code (0 ok, 1 failure,
2 usage error). Every call degrades to a caller-supplied fallback when the
binary is missing or misbehaves, so the embedded path remains the safety net.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

#: The runtime binary name.
BINARY_NAME = "marv-mlx"

#: Read-only verbs should never hang; lifecycle verbs can take a long time
#: (starting a model loads multi-GB weights), so they use their own timeout.
_QUERY_TIMEOUT = 15.0
_RUN_TIMEOUT = 30 * 60.0


def resolve_marv_mlx_binary(explicit: str | None = None) -> str | None:
    """Locate the ``marv-mlx`` binary, or None when it is not installed."""
    if explicit:
        return explicit
    return shutil.which(BINARY_NAME)


@dataclass(frozen=True, slots=True)
class RunnerStatus:
    """The runtime's view of the currently served model."""

    model: str
    port: int
    pid: int
    base_url: str

    @classmethod
    def from_json(cls, data: object) -> RunnerStatus | None:
        if not isinstance(data, dict):
            return None
        model = data.get("model")
        if not isinstance(model, str) or not model:
            return None
        port = data.get("port")
        pid = data.get("pid")
        base_url = data.get("base_url", "")
        return cls(
            model=model,
            port=int(port) if isinstance(port, (int, str)) else 0,
            pid=int(pid) if isinstance(pid, (int, str)) else 0,
            base_url=str(base_url),
        )


@dataclass(frozen=True, slots=True)
class ThinkingInfo:
    """Thinking metadata the runtime reports for a model."""

    control: str
    markers: str
    reasoning_first: bool

    @classmethod
    def from_json(cls, data: object) -> ThinkingInfo | None:
        if not isinstance(data, dict):
            return None
        control = data.get("thinking_control")
        markers = data.get("thinking_markers")
        if not isinstance(control, str) or not isinstance(markers, str):
            return None
        return cls(
            control=control,
            markers=markers,
            reasoning_first=bool(data.get("reasoning_first")),
        )


class MarvMlxCli:
    """Subprocess wrapper around the ``marv-mlx`` runtime CLI.

    Raises :class:`MarvMlxCliError` on a non-zero exit or unreadable output so
    callers can fall back to the embedded implementation.
    """

    def __init__(self, binary: str, *, query_timeout: float = _QUERY_TIMEOUT) -> None:
        self.binary = binary
        self.query_timeout = query_timeout

    def _run(self, args: Sequence[str], *, timeout: float) -> subprocess.CompletedProcess[str]:
        try:
            result = subprocess.run(
                [self.binary, *args],
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except (OSError, subprocess.SubprocessError) as err:
            raise MarvMlxCliError(f"marv-mlx {args[0] if args else ''} failed: {err}") from err
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            raise MarvMlxCliError(
                f"marv-mlx {' '.join(args)} exited {result.returncode}"
                + (f": {detail}" if detail else "")
            )
        return result

    def _run_json(self, args: Sequence[str]) -> object:
        result = self._run(args, timeout=self.query_timeout)
        text = result.stdout.strip()
        if not text:
            raise MarvMlxCliError(f"marv-mlx {' '.join(args)} returned no output")
        try:
            return json.loads(text)
        except ValueError as err:
            raise MarvMlxCliError(f"marv-mlx {' '.join(args)} returned invalid JSON") from err

    # ---- read-only queries ---------------------------------------------

    def status(self) -> RunnerStatus | None:
        """The currently served model, or None when the runtime is idle."""
        try:
            data = self._run_json(["status", "--json"])
        except MarvMlxCliError:
            # `status --json` prints `null` and exits 1 when nothing runs; an
            # exit-1 with a `null` body is "idle", not a failure.
            return None
        return RunnerStatus.from_json(data)

    def list_models(self) -> list[str]:
        """Model ids installed in the local hub, per the runtime."""
        data = self._run_json(["list", "--json"])
        if not isinstance(data, list):
            raise MarvMlxCliError("marv-mlx list --json did not return a list")
        return [entry for entry in data if isinstance(entry, str)]

    def info(self, model: str) -> ThinkingInfo | None:
        """Thinking metadata for `model`, or None when the runtime lacks it."""
        try:
            data = self._run_json(["info", model, "--json"])
        except MarvMlxCliError:
            return None
        return ThinkingInfo.from_json(data)

    # ---- lifecycle ------------------------------------------------------

    def run(self, model: str) -> None:
        """Start `model` and block until the runtime reports it ready."""
        self._run(["run", model], timeout=_RUN_TIMEOUT)

    def stop(self, model: str | None = None) -> None:
        """Stop `model` (or the default served model when None)."""
        args = ["stop"] if model is None else ["stop", model]
        self._run(args, timeout=_QUERY_TIMEOUT)


class MarvMlxCliError(RuntimeError):
    """Raised when a runtime CLI call fails in a way callers must handle."""
