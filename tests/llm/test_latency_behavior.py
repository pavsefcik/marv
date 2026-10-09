"""Behavior tests for turn-latency measurement."""

from __future__ import annotations

from marv.llm.latency import LatencyTracker, WaitEstimate, fit_slope, percentile


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


# --- calibration: the per-model fit that powers the wait prediction ---------


def test_fit_learns_a_prefill_rate_from_warm_samples() -> None:
    from marv.llm.latency import LatencyFit, LatencySample

    fit = LatencyFit.from_samples(
        [
            LatencySample(ttft_ms=2000.0, prompt_tokens=8000, schema_tokens=2000, streamed=True),
            LatencySample(ttft_ms=4000.0, prompt_tokens=18000, schema_tokens=2000, streamed=True),
        ]
    )

    # 30k tokens prefilled in 6s.
    assert fit.prefill_tokens_per_second == 5000.0
    assert fit.has_data


def test_fit_predicts_ttft_for_a_larger_prompt() -> None:
    from marv.llm.latency import LatencyFit, LatencySample

    fit = LatencyFit.from_samples(
        [LatencySample(ttft_ms=1000.0, prompt_tokens=5000, streamed=True)]
    )

    # The rate is 5000 tok/s, so 20k tokens is expected to take 4s.
    assert fit.predict_ttft_ms(20000) == 4000.0
    assert fit.predict_ttft_ms(20000, 0) == 4000.0


def test_fit_reports_no_eta_before_any_measured_turn() -> None:
    from marv.llm.latency import LatencyFit

    fit = LatencyFit()

    assert fit.has_data is False
    # The honest answer on a first-ever run: unknown, not a fabricated number.
    assert fit.predict_ttft_ms(30000) is None


def test_a_cache_hit_does_not_pull_the_rate_toward_an_optimistic_fit() -> None:
    from marv.llm.latency import LatencyFit, LatencySample

    fit = LatencyFit.from_samples(
        [
            LatencySample(ttft_ms=4000.0, prompt_tokens=20000, streamed=True),
            LatencySample(ttft_ms=8000.0, prompt_tokens=40000, streamed=True),
        ]
    )
    rate_before = fit.prefill_tokens_per_second

    # A server-side prefix cache makes a large prompt cheap: far below the fit.
    cached = fit.with_sample(LatencySample(ttft_ms=200.0, prompt_tokens=40000, streamed=True))

    assert cached.prefill_tokens_per_second == rate_before
    assert cached.cache_hits == 1
    assert cached.sample_count == fit.sample_count


def test_a_cold_sample_becomes_a_separate_cold_start_constant() -> None:
    from marv.llm.latency import LatencyFit, LatencySample

    # Warm up the rate first.
    fit = LatencyFit.from_samples(
        [LatencySample(ttft_ms=1000.0, prompt_tokens=5000, streamed=True)]
    )
    rate_before = fit.prefill_tokens_per_second

    # A cold turn: same prompt, but weight loading adds ~30s.
    cold = fit.with_sample(
        LatencySample(ttft_ms=31000.0, prompt_tokens=5000, streamed=True, cold=True)
    )

    # The cold turn must not visibly change the warm rate...
    assert cold.prefill_tokens_per_second == rate_before
    # ...and the extra time is learned as a cold-start constant.
    assert cold.cold_start_ms == 30000.0
    assert cold.cold_samples == 1
    assert cold.sample_count == fit.sample_count


def test_fit_round_trips_through_persisted_values() -> None:
    from marv.llm.latency import LatencyFit, LatencySample

    fit = LatencyFit.from_samples(
        [LatencySample(ttft_ms=2000.0, prompt_tokens=8000, streamed=True)]
    ).with_sample(LatencySample(ttft_ms=9000.0, prompt_tokens=8000, streamed=True, cold=True))

    restored = LatencyFit.from_dict(fit.as_dict())

    assert restored.prefill_tokens_per_second == fit.prefill_tokens_per_second
    assert restored.cold_start_ms == fit.cold_start_ms
    assert restored.sample_count == fit.sample_count
    assert restored.cold_samples == fit.cold_samples


def test_fit_from_dict_ignores_junk() -> None:
    from marv.llm.latency import LatencyFit

    restored = LatencyFit.from_dict({"total_prefill_tokens": "lots", "nope": True})

    assert restored.has_data is False
    assert restored.sample_count == 0


def test_tracker_fit_folds_its_own_samples() -> None:
    tracker = LatencyTracker()

    tracker.note_request(prompt_tokens=10000, schema_tokens=1000, now=0.0)
    tracker.note_first_token(now=2.0)
    tracker.note_request(prompt_tokens=20000, schema_tokens=1000, now=10.0)
    tracker.note_first_token(now=14.0)

    fit = tracker.fit()

    assert fit.sample_count == 2
    # 32k tokens prefilled in 6s (2s + 4s).
    assert fit.prefill_tokens_per_second == 32000.0 / 6.0


# --- turning a prediction into an exit offer --------------------------------


def _estimate(**kwargs: object) -> WaitEstimate:
    return WaitEstimate(**kwargs)  # type: ignore[arg-type]


def test_advice_is_silent_for_an_ordinary_wait() -> None:
    from marv.llm.latency import wait_advice

    # A short wait is not a decision, so it must not nag.
    assert wait_advice(_estimate(prompt_tokens=100, schema_tokens=100, eta_ms=1500.0)) is None


def test_advice_is_silent_when_there_is_no_estimate() -> None:
    from marv.llm.latency import wait_advice

    assert wait_advice(_estimate(prompt_tokens=50000, eta_ms=None)) is None


def test_advice_points_at_compaction_when_conversation_dominates() -> None:
    from marv.llm.latency import wait_advice

    advice = wait_advice(_estimate(prompt_tokens=40000, schema_tokens=2000, eta_ms=120_000.0))

    assert advice is not None
    assert "/compact" in advice
    assert "40,000" in advice
    assert "2:00" in advice


def test_advice_points_at_tool_schemas_when_schemas_dominate() -> None:
    from marv.llm.latency import wait_advice

    advice = wait_advice(_estimate(prompt_tokens=1000, schema_tokens=40_000, eta_ms=60_000.0))

    assert advice is not None
    assert "tool schemas" in advice
    assert "/compact" not in advice


def test_advice_is_honest_about_a_cold_start() -> None:
    from marv.llm.latency import wait_advice

    advice = wait_advice(
        _estimate(prompt_tokens=40000, schema_tokens=2000, eta_ms=60_000.0, cold=True)
    )

    assert advice is not None
    assert "cold" in advice
    # Nothing to shrink, so no fix is claimed.
    assert "/compact" not in advice


def test_estimate_attributes_the_dominant_cost() -> None:
    assert _estimate(prompt_tokens=1000, schema_tokens=100).dominant_cost == "prompt"
    assert _estimate(prompt_tokens=100, schema_tokens=1000).dominant_cost == "schemas"
    assert _estimate(prompt_tokens=1000, schema_tokens=100, cold=True).dominant_cost == "cold"


def test_prompt_share_is_the_conversation_fraction_of_prefill() -> None:
    estimate = _estimate(prompt_tokens=3000, schema_tokens=1000)

    assert estimate.prompt_share == 0.75
    assert _estimate().prompt_share == 0.0
