"""Plain-text and styled screen snapshots for the TUI.

Textual has no public "render the screen as text" API, so this borrows the
compositor it also uses to produce SVG screenshots. Snapshots are committed as
readable ``.txt`` files under ``snapshots/`` so a visual regression shows up as a
reviewable diff instead of a silent change. Regenerate them with
``MARV_UPDATE_SNAPSHOTS=1 make test``.

``snapshot_text`` pins layout and content. ``snapshot_styled`` additionally pins
the resolved styles (colour, bold, italic) each line uses, so a theme/CSS change
is caught rather than only its geometry.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from textual.app import App

SNAPSHOT_DIR = Path(__file__).parent / "snapshots"
UPDATE_ENV = "MARV_UPDATE_SNAPSHOTS"

# Values that are real at runtime but meaningless in a committed snapshot.
_VERSION_RE = re.compile(r"marv \d+\.\d+\.\d+")
_SESSION_RE = re.compile(r"session:[0-9a-f]+")
# The host memory figure moves with every test run. Kept on one line so it can
# never swallow the working directory on a wrapped status bar.
_RAM_RE = re.compile(r"  RAM [\d.]+[KMGT]?/[^\s]+ \([^)\n]* free\)")


def capture_screen(app: App[object]) -> str:
    """The full rendered terminal as text, trailing whitespace removed."""
    strips = app.screen._compositor.render_strips()  # type: ignore[attr-defined]
    return "\n".join(strip.text.rstrip() for strip in strips).rstrip("\n")


def normalize(text: str) -> str:
    """Replace volatile values (version, session id, RAM) with placeholders."""
    text = _VERSION_RE.sub("marv <version>", text)
    text = _SESSION_RE.sub("session:<id>", text)
    text = _RAM_RE.sub("", text)
    return text


def snapshot_text(app: App[object]) -> str:
    """Capture and normalize the app's screen for comparison."""
    return normalize(capture_screen(app))


def capture_styled(app: App[object]) -> str:
    """The screen as text plus the resolved styles each line uses.

    Each non-blank line is rendered as ``text || style | style``; the text is
    plain (so version/session/RAM masking still applies) and the styles come
    from the compositor, so theme variables are already resolved. Whitespace
    runs are dropped because they only pad, never signal.
    """
    strips = app.screen._compositor.render_strips()  # type: ignore[attr-defined]
    lines: list[str] = []
    for strip in strips:
        text = strip.text.rstrip()
        if not text.strip():
            lines.append("")
            continue
        styles: list[str] = []
        for segment in strip:
            if segment.text.strip() and segment.style is not None:
                style = str(segment.style)
                if style not in styles:
                    styles.append(style)
        lines.append(f"{text} || {' | '.join(styles)}")
    return "\n".join(lines).rstrip("\n")


def snapshot_styled(app: App[object]) -> str:
    """Capture and normalize the styled screen for comparison."""
    return normalize(capture_styled(app))


def assert_matches_snapshot(name: str, actual: str) -> None:
    """Compare ``actual`` with the committed ``snapshots/<name>.txt`` file.

    When ``MARV_UPDATE_SNAPSHOTS`` is set the snapshot is (re)written instead,
    so the human reviews the diff and commits it.
    """
    path = SNAPSHOT_DIR / f"{name}.txt"
    expected = f"{actual}\n"
    if os.environ.get(UPDATE_ENV):
        SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(expected, encoding="utf-8")
        return

    assert path.exists(), (
        f"missing snapshot {path.name}; run `{UPDATE_ENV}=1 make test` to create it"
    )
    committed = path.read_text(encoding="utf-8")
    assert actual == committed.rstrip("\n"), (
        f"snapshot {path.name} changed:\n"
        f"--- committed ---\n{committed}"
        f"--- actual ---\n{expected}\n"
        f"(run `{UPDATE_ENV}=1 make test` to update)"
    )
