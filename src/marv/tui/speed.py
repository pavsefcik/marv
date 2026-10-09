"""Live output-speed estimate for the status bar.

The rate is derived from streamed text using the active provider's token
counter, sampled periodically and smoothed so a single slow delta does not
make the number jump. Kept in the TUI layer: it is a display concern and needs
no support from the provider protocol.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

#: Minimum seconds between token-count samples.
SAMPLE_SECONDS = 0.5
#: Weight of the newest sample in the smoothed rate.
SMOOTHING = 0.3


class TokenSpeedTracker:
    """Estimate tokens generated per second while text streams in."""

    __slots__ = ("_count", "_text", "_last_tokens", "_last_time", "_rate", "_fresh")

    def __init__(self, count_tokens: Callable[[str], int]) -> None:
        self._count = count_tokens
        self._text = ""
        self._last_tokens = 0
        self._last_time: float | None = None
        self._rate = 0.0
        self._fresh = True

    @property
    def rate(self) -> float:
        """Last smoothed tokens-per-second estimate (0 before any sample)."""
        return self._rate

    def reset(self) -> None:
        """Start a new generation window, keeping the last displayed rate."""
        self._text = ""
        self._last_tokens = 0
        self._last_time = None
        self._fresh = True

    def add(self, text: str) -> float | None:
        """Record streamed `text`.

        Returns a new smoothed rate once enough time has passed to sample,
        otherwise None. The first call after ``reset`` only anchors the window,
        so idle time before generation does not count against the rate.
        """
        self._text += text
        now = time.monotonic()
        if self._last_time is None:
            self._last_tokens = self._count(self._text)
            self._last_time = now
            return None

        elapsed = now - self._last_time
        if elapsed < SAMPLE_SECONDS:
            return None

        tokens = self._count(self._text)
        instant = max(0.0, (tokens - self._last_tokens) / elapsed)
        if self._fresh:
            self._fresh = False
            self._rate = instant
        else:
            self._rate = (1 - SMOOTHING) * self._rate + SMOOTHING * instant
        self._last_tokens = tokens
        self._last_time = now
        return self._rate
