"""The interactive TUI's exit sign-off.

Printed after Textual has released the terminal, so the block lands at the top
of a fresh screen — the same sign-off marv-mlx leaves behind. Every row states
something marv actually observed (the session id, the model it unloaded, the
memory that release measured); nothing is invented. A model that is still
serving after the unload is reported as such rather than claimed as terminated.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import IO, Literal

# marv-mlx's accent, so the marv family signs off in the same magenta.
ACCENT = "\x1b[1;38;5;212m"
GREEN = "\x1b[38;5;42m"
YELLOW = "\x1b[38;5;226m"
DIM = "\x1b[2m"
RESET = "\x1b[0m"

CLEAR_SCREEN = "\x1b[2J\x1b[H"

#: Label column width, so the ✓/▲/· marks line up.
LABEL_WIDTH = 20

Kind = Literal["title", "ok", "warn", "dim"]
Line = tuple[Kind, str]


@dataclass(frozen=True, slots=True)
class GoodbyeFacts:
    """Facts the sign-off is allowed to print.

    ``unloaded`` and ``still_running`` are not interchangeable: the first means
    marv stopped a server, the second means one is (still) answering.
    """

    version: str
    session_id: str | None = None
    model: str | None = None
    port: int | None = None
    memory_bytes: int = 0
    unloaded: bool = False
    still_running: bool = False


def format_bytes(num_bytes: int) -> str:
    """Compact human-readable size, e.g. ``5.2 GB`` or ``640 MB``."""
    if num_bytes >= 1024**3:
        return f"{num_bytes / 1024**3:.1f} GB"
    if num_bytes >= 1024**2:
        return f"{num_bytes / 1024**2:.0f} MB"
    return f"{max(num_bytes, 0) // 1024} KB"


def _target(model: str | None, port: int | None) -> str:
    """``model :port`` (either half may be missing)."""
    if model and port is not None:
        return f"{model} :{port}"
    return model or (f":{port}" if port is not None else "")


def _row(marker: str, label: str, value: str = "") -> str:
    """One aligned ``marker label value`` row."""
    line = f"   {marker} {label:<{LABEL_WIDTH}}"
    return f"{line} {value}".rstrip()


def goodbye_lines(facts: GoodbyeFacts) -> list[Line]:
    """The sign-off as ``(kind, text)`` rows; the whole block in plain text."""
    lines: list[Line] = [("title", "\u258c\u258c marv says bye!")]

    if facts.session_id:
        lines.append(("ok", _row("\u2713", "session saved", facts.session_id)))

    target = _target(facts.model, facts.port)
    if facts.unloaded:
        lines.append(("ok", _row("\u2713", "model unloaded", target)))
        if facts.memory_bytes > 0:
            lines.append(
                ("ok", _row("\u2713", "memory released", format_bytes(facts.memory_bytes)))
            )
        lines.append(("dim", f"   all systems terminated \u00b7 marv {facts.version}"))
    elif facts.still_running:
        lines.append(("warn", _row("\u25b2", "model still running", target)))
        lines.append(("dim", "   run 'marv-mlx stop' to unload it"))
    else:
        lines.append(("dim", _row("\u00b7", "no model was running")))
        lines.append(("dim", f"   all systems terminated \u00b7 marv {facts.version}"))

    return lines


def goodbye_text(facts: GoodbyeFacts) -> str:
    """The sign-off without colour (non-TTY / ``NO_COLOR``)."""
    return "\n".join(text for _, text in goodbye_lines(facts))


def render_goodbye(facts: GoodbyeFacts, *, color: bool = True) -> str:
    """The sign-off, optionally tinted to match marv-mlx."""
    if not color:
        return goodbye_text(facts)

    styles: dict[Kind, str] = {"title": ACCENT, "ok": GREEN, "warn": YELLOW, "dim": DIM}
    return "\n".join(f"{styles[kind]}{text}{RESET}" for kind, text in goodbye_lines(facts))


def announce(
    facts: GoodbyeFacts,
    *,
    stream: IO[str] | None = None,
    color: bool | None = None,
) -> None:
    """Clear the screen and print the sign-off at the top (marv-mlx-style).

    ``color`` defaults to "TTY and NO_COLOR unset"; when it is off the block is
    still printed, just plain.
    """
    out = stream if stream is not None else sys.stdout
    if color is None:
        color = bool(getattr(out, "isatty", lambda: False)()) and not os.environ.get("NO_COLOR")

    out.write(CLEAR_SCREEN)
    out.write(render_goodbye(facts, color=color))
    out.write("\n")
    out.flush()
