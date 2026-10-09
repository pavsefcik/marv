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

import json
import math
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Sequence

#: Milliseconds per second, for turning monotonic deltas into display units.
_MS = 1000.0

#: Rough characters-per-token ratio used for transport-side estimates. Exact
#: per-model tokenization is unnecessary: the number only has to be stable and
#: comparable turn to turn, which is what a regression check needs.
CHARS_PER_TOKEN = 4

#: Below this observed TTFT a sample is treated as too cheap to inform the
#: prefill rate (an empty prompt, or a server-side prefix cache hit).
MIN_PREFILL_MS = 50.0

#: A sample this much faster than the current prediction is a cache hit, not
#: evidence that the prefill rate improved. Treating it as noise would let one
#: cached turn drag the whole fit toward an optimistic, wrong ETA.
CACHE_HIT_RATIO = 0.5


def estimate_tokens_from_json(payload: object) -> int:
    """Estimate token count from a serialized payload (chars / ratio).

    Shared by the transport (prompt and tool-schema estimates) and the agent
    (tool-schema estimate for the wait prediction) so both layers report the
    same number for the same bytes.
    """
    try:
        return len(json.dumps(payload)) // CHARS_PER_TOKEN
    except (TypeError, ValueError):
        return 0


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
    #: True when the model had to be loaded/served for this turn, so the sample
    #: includes weight loading and must not be folded into the prefill rate.
    cold: bool = False

    @property
    def prefill_tokens(self) -> int:
        """Prompt + tool-schema tokens the server had to prefill."""
        return self.prompt_tokens + self.schema_tokens


@dataclass(slots=True)
class LatencyFit:
    """Per-model prefill constants learned from this machine's own turns.

    ``TTFT ~= prefill_tokens / rate + cold_start``, with the two regimes kept
    apart: a *cold* start (weights loading — the dominant cost on the first turn
    after an unload) is learned as a separate constant and excluded from the
    rate, and a sample far below the current prediction is counted as a
    server-side *cache hit* rather than treated as a faster prefill.

    The rate is a through-origin aggregate over warm samples
    (``total_tokens / total_seconds``) rather than a two-point slope, so a
    handful of noisy samples cannot produce an absurd estimate.
    """

    total_prefill_tokens: int = 0
    total_prefill_ms: float = 0.0
    cold_start_ms: float = 0.0
    sample_count: int = 0
    cold_samples: int = 0
    cache_hits: int = 0
    updated_at: float = 0.0

    @property
    def prefill_tokens_per_second(self) -> float:
        """Warm prefill rate in tokens/second (0.0 before any usable sample)."""
        if self.total_prefill_ms <= 0 or self.total_prefill_tokens <= 0:
            return 0.0
        return self.total_prefill_tokens / (self.total_prefill_ms / _MS)

    @property
    def has_data(self) -> bool:
        """Whether a prediction can be made from measured turns."""
        return self.prefill_tokens_per_second > 0

    def predict_prefill_ms(self, prefill_tokens: int) -> float:
        """Milliseconds this many prefill tokens are expected to take."""
        rate = self.prefill_tokens_per_second
        if rate <= 0 or prefill_tokens <= 0:
            return 0.0
        return (prefill_tokens / rate) * _MS

    def predict_ttft_ms(
        self,
        prompt_tokens: int,
        schema_tokens: int = 0,
        *,
        cold: bool = False,
    ) -> float | None:
        """Predicted time-to-first-token, or None with no measured history.

        Returning None is the honest answer on a first-ever run: the UI then
        degrades to elapsed-only instead of showing a fabricated ETA.
        """
        if not self.has_data:
            return None
        eta = self.predict_prefill_ms(prompt_tokens + schema_tokens)
        if cold:
            eta += self.cold_start_ms
        return eta

    def with_sample(
        self,
        sample: LatencySample,
        *,
        now: float | None = None,
    ) -> LatencyFit:
        """Return a fit refined by one measured turn (pure)."""
        if not sample.streamed or sample.ttft_ms <= 0:
            return self

        if sample.cold:
            # Learn the cold-start constant only once the warm rate exists,
            # otherwise there is nothing to subtract the prefill cost from.
            if not self.has_data:
                return LatencyFit(
                    total_prefill_tokens=self.total_prefill_tokens,
                    total_prefill_ms=self.total_prefill_ms,
                    cold_start_ms=self.cold_start_ms,
                    sample_count=self.sample_count,
                    cold_samples=self.cold_samples + 1,
                    cache_hits=self.cache_hits,
                    updated_at=time.time() if now is None else now,
                )
            prefill_ms = self.predict_prefill_ms(sample.prefill_tokens)
            observed = max(0.0, sample.ttft_ms - prefill_ms)
            # Blend toward the new observation instead of jumping to it.
            cold_start_ms = (
                observed if self.cold_samples == 0 else (self.cold_start_ms + observed) / 2
            )
            return LatencyFit(
                total_prefill_tokens=self.total_prefill_tokens,
                total_prefill_ms=self.total_prefill_ms,
                cold_start_ms=cold_start_ms,
                sample_count=self.sample_count,
                cold_samples=self.cold_samples + 1,
                cache_hits=self.cache_hits,
                updated_at=time.time() if now is None else now,
            )

        if sample.prefill_tokens <= 0 or sample.ttft_ms < MIN_PREFILL_MS:
            return self

        if self.has_data:
            predicted = self.predict_prefill_ms(sample.prefill_tokens)
            if predicted > 0 and sample.ttft_ms < predicted * CACHE_HIT_RATIO:
                # A server-side prefix cache made a large prompt cheap. Keep it
                # out of the rate: it is a real speedup but not a new prefill
                # baseline, and folding it in would under-predict every later
                # uncached turn.
                return LatencyFit(
                    total_prefill_tokens=self.total_prefill_tokens,
                    total_prefill_ms=self.total_prefill_ms,
                    cold_start_ms=self.cold_start_ms,
                    sample_count=self.sample_count,
                    cold_samples=self.cold_samples,
                    cache_hits=self.cache_hits + 1,
                    updated_at=time.time() if now is None else now,
                )

        return LatencyFit(
            total_prefill_tokens=self.total_prefill_tokens + sample.prefill_tokens,
            total_prefill_ms=self.total_prefill_ms + sample.ttft_ms,
            cold_start_ms=self.cold_start_ms,
            sample_count=self.sample_count + 1,
            cold_samples=self.cold_samples,
            cache_hits=self.cache_hits,
            updated_at=time.time() if now is None else now,
        )

    @classmethod
    def from_samples(cls, samples: Sequence[LatencySample]) -> LatencyFit:
        """Fold a sequence of measured samples into a fresh fit."""
        fit = cls()
        for sample in samples:
            fit = fit.with_sample(sample)
        return fit

    def as_dict(self) -> dict[str, Any]:
        """Serializable view for ``state.toml``."""
        return {
            "total_prefill_tokens": self.total_prefill_tokens,
            "total_prefill_ms": round(self.total_prefill_ms, 3),
            "cold_start_ms": round(self.cold_start_ms, 3),
            "sample_count": self.sample_count,
            "cold_samples": self.cold_samples,
            "cache_hits": self.cache_hits,
            "updated_at": round(self.updated_at, 3),
        }

    @classmethod
    def from_dict(cls, data: object) -> LatencyFit:
        """Rebuild a fit from persisted values, ignoring junk."""
        if not isinstance(data, dict):
            return cls()

        def number(key: str) -> float:
            value = data.get(key)
            if isinstance(value, bool):
                return 0.0
            if isinstance(value, (int, float)):
                return float(value)
            return 0.0

        return cls(
            total_prefill_tokens=int(number("total_prefill_tokens")),
            total_prefill_ms=number("total_prefill_ms"),
            cold_start_ms=number("cold_start_ms"),
            sample_count=int(number("sample_count")),
            cold_samples=int(number("cold_samples")),
            cache_hits=int(number("cache_hits")),
            updated_at=number("updated_at"),
        )


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


@dataclass(slots=True)
class WaitEstimate:
    """A pre-request prediction of how long the first token will take.

    Computed before the request is sent (so the UI can narrate the wait from
    its very first second) and published to delivery on the agent chunk stream.
    ``eta_ms is None`` is a first-class value: on a machine with no measured
    turns the UI must degrade to elapsed-only, never to a fabricated number.
    """

    prompt_tokens: int = 0
    schema_tokens: int = 0
    eta_ms: float | None = None
    cold: bool = False

    @property
    def prefill_tokens(self) -> int:
        """Prompt + tool-schema tokens this turn must prefill."""
        return self.prompt_tokens + self.schema_tokens

    @property
    def has_eta(self) -> bool:
        """Whether a measured prediction is available."""
        return self.eta_ms is not None


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
        cold: bool = False,
        now: float | None = None,
    ) -> None:
        """Record that a request is being sent; anchors the TTFT clock."""
        self._pending = LatencySample(
            prompt_tokens=prompt_tokens,
            schema_tokens=schema_tokens,
            cold=cold,
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

    def refine_last_schema_tokens(self, schema_tokens: int) -> None:
        """Refine the last sample's tool-schema cost from the transport.

        The transport measures the serialized ``tools`` payload it actually put
        on the wire, which is more accurate than the agent's pre-send estimate
        (compat settings can rewrite it).
        """
        sample = self.last_sample
        if sample is not None and sample.streamed and schema_tokens >= 0:
            sample.schema_tokens = schema_tokens

    def fit(self) -> LatencyFit:
        """The per-model calibration implied by this run's samples."""
        return LatencyFit.from_samples(self._metrics.samples)

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
