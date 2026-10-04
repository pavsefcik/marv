"""Theme resolution for the TUI.

The app never builds hex palettes: it selects one of Textual's built-in theme
names (all of which Textual registers itself), or marv's ``minimal`` theme,
which ``AgentApp`` registers. ``auto`` is resolved once at startup by probing
whether the terminal background is dark.
"""

from __future__ import annotations

import contextlib
import os
import re
import select
import subprocess
import sys
import termios
import time

from textual.theme import BUILTIN_THEMES

AUTO_THEME = "auto"
MINIMAL_THEME_NAME = "minimal"
DARK_THEME = "ansi-dark"
LIGHT_THEME = "ansi-light"

# Bounds on the two probes. Together they keep startup detection under ~600 ms
# even when the terminal never answers and `defaults` is slow.
OSC11_TIMEOUT_SECONDS = 0.25
APPEARANCE_TIMEOUT_SECONDS = 0.25

# An OSC 11 query ("what is your background colour?"), BEL-terminated.
_OSC11_QUERY = "\x1b]11;?\x07"
_OSC11_REPLY = re.compile(r"\x1b\]11;(?P<spec>[^\x07\x1b]+)")
_HEX_CHANNEL = re.compile(r"[0-9a-fA-F]{1,4}")

_cached_appearance: bool | None = None
_appearance_cached = False


def resolve_theme_name(configured: str | None) -> tuple[str, str | None]:
    """Resolve the configured theme to a registered Textual theme name.

    Returns ``(theme_name, warning)``; ``warning`` is a message to surface to
    the user, or ``None`` when the configured value was honoured. Unknown names
    fall back to ``auto`` instead of raising.
    """
    name = (configured or "").strip()
    if not name or name == AUTO_THEME:
        return _auto_theme_name(), None
    if name == MINIMAL_THEME_NAME or name in BUILTIN_THEMES:
        return name, None
    return _auto_theme_name(), f"unknown theme {name!r}: falling back to auto"


def detect_dark_appearance() -> bool | None:
    """Whether the terminal/appearance is dark, cached after the first probe.

    ``None`` means the appearance could not be determined. Never raises: a
    terminal that does not answer and an unreadable system appearance simply
    fall through to the next probe.
    """
    global _cached_appearance, _appearance_cached
    if _appearance_cached:
        return _cached_appearance

    appearance = _query_terminal_appearance()
    if appearance is None:
        appearance = _system_appearance()
    _cached_appearance = appearance
    _appearance_cached = True
    return appearance


def reset_appearance_cache() -> None:
    """Forget the cached appearance so the next lookup probes again."""
    global _cached_appearance, _appearance_cached
    _cached_appearance = None
    _appearance_cached = False


def _auto_theme_name() -> str:
    """The ANSI theme matching the detected appearance (dark is the default)."""
    return LIGHT_THEME if detect_dark_appearance() is False else DARK_THEME


def _query_terminal_appearance() -> bool | None:
    """Ask the terminal for its background colour with an OSC 11 query.

    Returns ``True`` for a dark background, ``False`` for a light one, and
    ``None`` when there is no TTY, the terminal does not reply in time, or the
    reply cannot be parsed.
    """
    try:
        if not (sys.stdin.isatty() and sys.stdout.isatty()):
            return None
        fd = sys.stdin.fileno()
        original = termios.tcgetattr(fd)
    except (AttributeError, OSError, ValueError, termios.error):
        return None

    try:
        raw = list(original)
        raw[3] &= ~(termios.ECHO | termios.ICANON)
        raw[6] = list(original[6])
        raw[6][termios.VMIN] = 0
        raw[6][termios.VTIME] = 1
        termios.tcsetattr(fd, termios.TCSANOW, raw)
        sys.stdout.write(_OSC11_QUERY)
        sys.stdout.flush()
        return _parse_osc11_response(_read_osc11_reply(fd))
    except Exception:  # noqa: BLE001 - detection must never break startup
        return None
    finally:
        with contextlib.suppress(OSError, ValueError, termios.error):
            termios.tcsetattr(fd, termios.TCSANOW, original)


def _read_osc11_reply(fd: int) -> bytes:
    """Read the OSC 11 reply until its terminator or the timeout, else ``b""``."""
    deadline = time.monotonic() + OSC11_TIMEOUT_SECONDS
    buffer = b""
    while (remaining := deadline - time.monotonic()) > 0:
        try:
            ready, _, _ = select.select([fd], [], [], remaining)
        except (OSError, ValueError):
            return buffer
        if not ready:
            return buffer
        try:
            chunk = os.read(fd, 64)
        except OSError:
            return buffer
        if not chunk:
            return buffer
        buffer += chunk
        if buffer.endswith(b"\x07") or buffer.endswith(b"\x1b\\"):
            return buffer
    return buffer


def _parse_osc11_response(buffer: bytes) -> bool | None:
    """Whether an OSC 11 reply reports a dark background."""
    match = _OSC11_REPLY.search(buffer.decode("ascii", errors="replace"))
    if match is None:
        return None
    channels = _parse_color_spec(match.group("spec").strip())
    if channels is None:
        return None
    red, green, blue = channels
    luminance = (0.2126 * red + 0.7152 * green + 0.0722 * blue) / 255
    return luminance < 0.5


def _parse_color_spec(spec: str) -> tuple[float, float, float] | None:
    """Parse an OSC 11 colour spec (``rgb:RRRR/GGGG/BBBB``, ``#rgb``, ``#rrggbb``)."""
    if spec.startswith("rgb:"):
        parts = spec.removeprefix("rgb:").split("/")
    elif spec.startswith("#"):
        digits = spec[1:]
        if len(digits) == 3:
            digits = "".join(2 * digit for digit in digits)
        parts = [digits[index : index + 2] for index in range(0, len(digits), 2)]
    else:
        return None

    if len(parts) != 3 or not all(_HEX_CHANNEL.fullmatch(part) for part in parts):
        return None
    red, green, blue = parts
    return (_channel_to_255(red), _channel_to_255(green), _channel_to_255(blue))


def _channel_to_255(part: str) -> float:
    """Scale a 1-4 digit hex channel to the 0-255 range."""
    maximum = float(16 ** len(part) - 1)
    return float(int(part, 16)) / maximum * 255.0


def _system_appearance() -> bool | None:
    """Ask macOS for the system appearance via ``defaults``.

    Returns ``None`` when the appearance cannot be read (not macOS, missing
    ``defaults`` binary, or a timeout), so the caller falls back to dark.
    """
    if sys.platform != "darwin":
        return None

    try:
        completed = subprocess.run(
            ["defaults", "read", "-g", "AppleInterfaceStyle"],
            capture_output=True,
            text=True,
            timeout=APPEARANCE_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    if completed.returncode == 0:
        return completed.stdout.strip().lower() == "dark"
    if "does not exist" in completed.stderr:
        # macOS only stores the key in dark mode, so a missing key means light.
        return False
    return None
