"""Status bar widget showing model and working directory."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from textual.containers import Horizontal
from textual.widgets import Static

from marv.runtime.settings import InteractionMode
from marv.tui.chat import format_clock

if TYPE_CHECKING:
    from textual.app import ComposeResult

    from marv.runtime.settings import ThinkingLevel
    from marv.tui.memory import MemoryUsage


class StatusBar(Horizontal):
    """Status bar with left (model + tokens) and right (cwd) sections."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._model = "unknown"
        self._thinking = "off"
        self._interaction_mode: InteractionMode | None = None
        self._tokens = 0
        self._max_tokens = 0
        self._speed = 0.0
        self._ttft_ms = 0.0
        self._prefill_tokens = 0
        self._eta_ms: float | None = None
        self._elapsed_seconds: float | None = None
        self._extension_status: str | None = None
        self._session_id: str | None = None
        self._session_parent: str | None = None
        self._memory: MemoryUsage | None = None

    def compose(self) -> ComposeResult:
        yield Static(id="status-left")
        yield Static(id="status-right")

    def on_mount(self) -> None:
        self._update_display()

    def _update_display(self) -> None:
        left = self._model

        if self._session_id:
            left += f"  session:{self._session_id}"
            if self._session_parent:
                left += f" (fork:{self._session_parent})"

        # Show thinking level if not off
        if self._thinking != "off":
            left += f" [thinking:{self._thinking}]"

        # A durable marker that tools are off; the mode change is otherwise
        # only announced once in the chat stream.
        if self._interaction_mode is InteractionMode.CHAT:
            # Escaped: a bare ``[chat]`` is parsed as markup and swallowed.
            left += r"  \[chat]"

        # Add token usage next to model
        if self._max_tokens > 0:
            pct = (self._tokens / self._max_tokens) * 100
            left += f"  {self._tokens:,}/{self._max_tokens:,} ({pct:.0f}%)"
        elif self._tokens > 0:
            left += f"  {self._tokens:,} tokens"

        if self._speed > 0:
            left += f"  {self._speed:.0f} tok/s"

        # TTFT is the number that decides whether the model feels responsive;
        # prefill tokens explain it, so they are shown next to it. The
        # prediction (when a measured fit exists) sits beside the reading it
        # was checked against, and is omitted entirely while unknown.
        readings: list[str] = []
        if self._ttft_ms > 0:
            reading = f"ttft {self._ttft_ms:.0f}ms"
            if self._prefill_tokens > 0:
                reading += f"/{self._prefill_tokens:,}t"
            readings.append(reading)
        if self._eta_ms is not None:
            readings.append(f"eta ~{format_clock(self._eta_ms / 1000)}")
        if readings:
            left += "  " + " ".join(readings)

        # While decoding, how long this turn has been generating so far.
        if self._elapsed_seconds is not None:
            left += f"  {format_clock(self._elapsed_seconds)} elapsed"

        if self._memory is not None and self._memory.total > 0:
            left += f"  {self._memory.label()}"

        if self._extension_status:
            left += f"  status:{self._extension_status}"

        home = os.path.expanduser("~")
        cwd = os.getcwd()
        if cwd.startswith(home + os.sep) or cwd == home:
            cwd = "~" + cwd[len(home) :]
        self.query_one("#status-left", Static).update(left)
        self.query_one("#status-right", Static).update(cwd)

    def set_model(self, model: str) -> None:
        """Set the model name."""
        self._model = model
        self._update_display()

    def set_thinking(self, level: ThinkingLevel) -> None:
        """Set the thinking level display."""
        self._thinking = level.value
        self._update_display()

    def set_interaction_mode(self, mode: InteractionMode) -> None:
        """Show whether the agent is in plain chat mode."""
        self._interaction_mode = mode
        self._update_display()

    def set_tokens(self, tokens: int, max_tokens: int = 0) -> None:
        """Set the token count."""
        self._tokens = tokens
        self._max_tokens = max_tokens
        self._update_display()

    def set_speed(self, tokens_per_second: float) -> None:
        """Set the estimated generation speed in tokens per second."""
        self._speed = tokens_per_second
        self._update_display()

    def set_ttft(self, ttft_ms: float, prefill_tokens: int = 0) -> None:
        """Set the last time-to-first-token and its prompt cost."""
        self._ttft_ms = ttft_ms
        self._prefill_tokens = prefill_tokens
        self._update_display()

    def set_eta(self, eta_ms: float | None) -> None:
        """Set (or clear, with None) the pending turn's predicted TTFT."""
        self._eta_ms = eta_ms
        self._update_display()

    def set_elapsed(self, seconds: float | None) -> None:
        """Set (or clear, with None) how long the current turn has generated."""
        self._elapsed_seconds = seconds
        self._update_display()

    def set_session(self, session_id: str, parent_id: str | None = None) -> None:
        """Set the session display."""
        self._session_id = session_id
        self._session_parent = parent_id
        self._update_display()

    def set_extension_status(self, text: str | None) -> None:
        """Set extension-provided status text."""
        self._extension_status = text
        self._update_display()

    def set_memory(self, usage: MemoryUsage | None) -> None:
        """Set the host memory reading shown in the status bar."""
        self._memory = usage
        self._update_display()
