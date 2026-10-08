"""Turn-latency measurement primitives.

TTFT is the number that decides whether a local model feels like a
coworking partner, so it is measured rather than guessed. A sample pairs the
time from sending a request to the first streamed token with the prompt cost
that request had to prefill, which is what makes a regression attributable:
TTFT that grows across turns is a prefill problem, not an HTTP problem.

The module is pure -- the only clock is ``time.monotonic`` behind an
injectable ``now`` argument -- so both the TUI and ``marv bench`` can share
one definition of the number.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

#: Milliseconds per second, for turning monotonic deltas into display units.
_MS = 1000.0


def percentile(sorted_values: Sequence[float], quantile: float) -> float:
    """Nearest-rank percentile of an already-sorted, non-empty sequence.

    Nearest-rank (rather than interpolation) keeps the reported TTFT a real
    measured turn, never a value no turn actually took.
    """
    if not sorted_values:
        return 0.0
    rank = math.ceil(quantile * len(sorted_values))
    index = min(max(rank - 1, 0), len(sorted_values) - 1)
    return sorted_values[index]


def fit_slope(values: Sequence[float]) -> float:
    """Least-squares slope of `values` against their index.

    Used for "how fast is TTFT growing per turn of conversation?". A flat
    conversation sits near 0; a conversation whose prompt is re-prefilled in
    full each turn climbs roughly linearly. Returns 0.0 for fewer than two
    points (a slope needs two) or for a constant series.
    """
    count = len(values)
    if count < 2:
        return 0.0
    mean_x = (count - 1) / 2
    mean_y = sum(values) / count
    variance_x = sum((i - mean_x) ** 2 for i in range(count))
    if variance_x == 0:
        return 0.0
    covariance = sum((i - mean_x) * (y - mean_y) for i, y in enumerate(values))
    return covariance / variance_x


@dataclass(slots=True)
class LatencySample:
    """One streamed turn's time-to-first-token and the prompt cost behind it."""

    ttft_ms: float = 0.0
    prompt_tokens: int = 0
    schema_tokens: int = 0
    streamed: bool = False

    @property
    def prefill_tokens(self) -> int:
        """Prompt + tool-schema tokens the server had to prefill."""
        return self.prompt_tokens + self.schema_tokens


@dataclass(slots=True)
class LatencyMetrics:
    """Aggregate view over one run's latency samples."""

    samples: list[LatencySample] = field(default_factory=list)

    def _streamed(self) -> list[LatencySample]:
        """Samples that actually produced a token (errored turns excluded)."""
        return [sample for sample in self.samples if sample.streamed]

    @property
    def turns(self) -> int:
        """Number of turns that produced output."""
        return len(self._streamed())

    @property
    def ttft_ms(self) -> float:
        """Median time-to-first-token, in milliseconds."""
        values = sorted(sample.ttft_ms for sample in self._streamed())
        return percentile(values, 0.5)

    @property
    def ttft_p90_ms(self) -> float:
        """90th-percentile time-to-first-token, in milliseconds."""
        values = sorted(sample.ttft_ms for sample in self._streamed())
        return percentile(values, 0.9)

    @property
    def prefill_tokens(self) -> int:
        """Median prompt + schema tokens prefill-fed per turn."""
        values = sorted(float(sample.prefill_tokens) for sample in self._streamed())
        return int(percentile(values, 0.5))

    @property
    def prompt_tokens_per_turn(self) -> float:
        """Mean prompt tokens (excluding tool schemas) sent per turn."""
        streamed = self._streamed()
        if not streamed:
            return 0.0
        return sum(sample.prompt_tokens for sample in streamed) / len(streamed)

    @property
    def ttft_growth_ms_per_turn(self) -> float:
        """Change in TTFT per additional turn of conversation.

        The headline diagnostic: a value near zero means prefill cost is not
        growing with the conversation.
        """
        streamed = self._streamed()
        if len(streamed) < 2:
            return 0.0
        return fit_slope([sample.ttft_ms for sample in streamed])


class LatencyTracker:
    """Accumulate TTFT samples for one agent run.

    One turn is: ``note_request`` when the request goes out, then
    ``note_first_token`` when its first token arrives. A turn that never
    streams is dropped rather than recorded as an infinite TTFT.
    """

    __slots__ = ("_metrics", "_pending", "_requested_at")

    def __init__(self) -> None:
        self._metrics = LatencyMetrics()
        self._pending: LatencySample | None = None
        self._requested_at: float | None = None

    def reset(self) -> None:
        """Forget every sample and any in-flight request."""
        self._metrics = LatencyMetrics()
        self._pending = None
        self._requested_at = None

    def note_request(
        self,
        prompt_tokens: int,
        schema_tokens: int = 0,
        *,
        now: float | None = None,
    ) -> None:
        """Record that a request is being sent; anchors the TTFT clock."""
        self._pending = LatencySample(
            prompt_tokens=prompt_tokens,
            schema_tokens=schema_tokens,
        )
        self._requested_at = time.monotonic() if now is None else now

    def note_first_token(self, *, now: float | None = None) -> float | None:
        """Record the first token of the in-flight request.

        Returns the sample's TTFT in milliseconds, or None when no request is
        in flight (or it already produced a token).
        """
        if self._pending is None or self._requested_at is None:
            return None
        current = time.monotonic() if now is None else now
        elapsed = max(0.0, (current - self._requested_at) * _MS)
        self._pending.ttft_ms = elapsed
        self._pending.streamed = True
        self._metrics.samples.append(self._pending)
        self._pending = None
        self._requested_at = None
        return elapsed

    def discard_pending(self) -> None:
        """Drop an in-flight request that never produced a token.

        Called when a turn errors before streaming so a failed request is not
        later mistaken for the next turn's TTFT.
        """
        self._pending = None
        self._requested_at = None

    def refine_last_ttft(self, ttft_ms: float) -> None:
        """Replace the last sample's TTFT with a more precise measurement.

        A provider that times the request inside its own transport reports a
        TTFT that excludes the agent loop's bookkeeping, so it is preferred
        over the local wall clock. Only the most recent streamed sample is
        touched, so a late report cannot rewrite history.
        """
        sample = self.last_sample
        if sample is not None and sample.streamed:
            sample.ttft_ms = max(0.0, ttft_ms)

    @property
    def last_sample(self) -> LatencySample | None:
        """The most recently completed sample, or None before any turn."""
        if not self._metrics.samples:
            return None
        return self._metrics.samples[-1]

    @property
    def metrics(self) -> LatencyMetrics:
        """Samples recorded so far."""
        return self._metrics
