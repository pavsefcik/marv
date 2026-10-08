"""Behavior tests for turn-latency measurement."""

from __future__ import annotations

from marv.llm.latency import LatencyTracker, fit_slope, percentile


def test_ttft_is_measured_from_request_to_first_token() -> None:
    tracker = LatencyTracker()

    tracker.note_request(prompt_tokens=1000, now=10.0)
    ttft = tracker.note_first_token(now=10.25)

    assert ttft == 250.0
    assert tracker.metrics.turns == 1
    assert tracker.metrics.ttft_ms == 250.0
    assert tracker.metrics.prefill_tokens == 1000


def test_only_the_first_token_of_a_turn_counts() -> None:
    tracker = LatencyTracker()

    tracker.note_request(prompt_tokens=100, now=0.0)
    tracker.note_first_token(now=0.5)
    # Later tokens of the same turn must not add samples or move the result.
    assert tracker.note_first_token(now=9.0) is None

    assert tracker.metrics.turns == 1
    assert tracker.metrics.ttft_ms == 500.0


def test_first_token_without_a_request_records_nothing() -> None:
    tracker = LatencyTracker()

    assert tracker.note_first_token(now=1.0) is None
    assert tracker.metrics.turns == 0


def test_a_turn_that_errors_is_not_counted_as_a_sample() -> None:
    tracker = LatencyTracker()

    # Turn 1 errors before streaming; only turn 2 produces a token.
    tracker.note_request(prompt_tokens=100, now=0.0)
    tracker.discard_pending()
    tracker.note_request(prompt_tokens=200, now=10.0)
    tracker.note_first_token(now=11.0)

    assert tracker.metrics.turns == 1
    assert tracker.metrics.ttft_ms == 1000.0
    assert tracker.metrics.prompt_tokens_per_turn == 200.0


def test_schema_tokens_are_added_to_the_reported_prefill_cost() -> None:
    tracker = LatencyTracker()

    tracker.note_request(prompt_tokens=1000, schema_tokens=2000, now=0.0)
    tracker.note_first_token(now=0.1)

    assert tracker.metrics.prefill_tokens == 3000


def test_a_provider_reported_ttft_refines_the_last_sample() -> None:
    tracker = LatencyTracker()

    tracker.note_request(prompt_tokens=10, now=0.0)
    tracker.note_first_token(now=1.0)
    tracker.refine_last_ttft(12.5)

    assert tracker.metrics.ttft_ms == 12.5


def test_ttft_growth_is_positive_when_later_turns_are_slower() -> None:
    tracker = LatencyTracker()

    for ttft in [100.0, 200.0, 300.0]:
        tracker.note_request(prompt_tokens=100, now=0.0)
        tracker.note_first_token(now=ttft / 1000)

    # +100 ms per turn of conversation.
    assert tracker.metrics.ttft_growth_ms_per_turn == 100.0


def test_ttft_growth_is_flat_when_turns_do_not_degrade() -> None:
    tracker = LatencyTracker()

    for _ in range(3):
        tracker.note_request(prompt_tokens=100, now=0.0)
        tracker.note_first_token(now=0.25)

    assert tracker.metrics.ttft_growth_ms_per_turn == 0.0


def test_reset_clears_samples_and_in_flight_request() -> None:
    tracker = LatencyTracker()

    tracker.note_request(prompt_tokens=100, now=0.0)
    tracker.note_first_token(now=0.5)
    tracker.note_request(prompt_tokens=100, now=1.0)
    tracker.reset()

    assert tracker.metrics.turns == 0
    assert tracker.last_sample is None
    # The discarded in-flight request must not be attributed to a later token.
    assert tracker.note_first_token(now=2.0) is None


def test_percentile_uses_nearest_rank() -> None:
    values = [10.0, 20.0, 30.0, 40.0]

    assert percentile(values, 0.5) == 20.0
    assert percentile(values, 0.9) == 40.0
    assert percentile([], 0.5) == 0.0


def test_fit_slope_needs_at_least_two_points() -> None:
    assert fit_slope([100.0]) == 0.0
    assert fit_slope([]) == 0.0
