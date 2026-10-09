"""Latency benchmark for the active model.

Answers the only question that matters for a local-LLM coworking harness:
*how long until the first token, and does that get worse as the conversation
grows?* Both halves are needed — a fast first turn that degrades by turn ten is
the failure mode of re-prefilling the whole history every turn.

The command runs a scripted conversation through the real agent loop, so what
it measures is the shipping path (prompt assembly, tool schemas, transport),
not a synthetic HTTP probe.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

from marv.config.state import remember_latency_fit
from marv.llm.latency import LatencyFit, LatencyMetrics

if TYPE_CHECKING:
    from collections.abc import Callable

    from marv.config import Config
    from marv.llm.provider import LLMProvider

#: Turns used when the caller gives no prompts. Deliberately generic questions
#: so the benchmark is meaningful for a coworking workload, and deliberately
#: multi-turn so the growth slope is measurable.
DEFAULT_PROMPTS: tuple[str, ...] = (
    "Reply with a single short sentence about the weather.",
    "Reply with a single short sentence about a good cup of coffee.",
    "Reply with a single short sentence about why the sky is blue.",
    "Reply with a single short sentence about a well-organised desk.",
    "Reply with a single short sentence about a long train journey.",
)

#: A trivial turn whose only job is to warm the server before measurement.
WARMUP_PROMPT = "hi"


@dataclass(slots=True)
class BenchReport:
    """Latency results for one benchmark run."""

    provider: str
    model: str
    prompts: list[str] = field(default_factory=list)
    metrics: LatencyMetrics = field(default_factory=LatencyMetrics)
    #: The per-model calibration these samples imply. ``bench`` is where a
    #: fresh machine seeds it, so the wait predictor has measured constants
    #: rather than a hardcoded guess.
    fit: LatencyFit = field(default_factory=LatencyFit)

    def to_dict(self) -> dict[str, Any]:
        """Serializable view for ``--json`` consumers."""
        return {
            "provider": self.provider,
            "model": self.model,
            "turns": self.metrics.turns,
            "calibration": {
                "prefill_tokens_per_second": round(self.fit.prefill_tokens_per_second, 2),
                "cold_start_ms": round(self.fit.cold_start_ms, 1),
                "samples": self.fit.sample_count,
                "cold_samples": self.fit.cold_samples,
                "cache_hits": self.fit.cache_hits,
            },
            "ttft_ms": {
                "p50": round(self.metrics.ttft_ms, 1),
                "p90": round(self.metrics.ttft_p90_ms, 1),
                "growth_per_turn": round(self.metrics.ttft_growth_ms_per_turn, 1),
            },
            "prompt_tokens": {
                "median_prefill": self.metrics.prefill_tokens,
                "mean_per_turn": round(self.metrics.prompt_tokens_per_turn, 1),
            },
            "samples": [
                {
                    "ttft_ms": round(sample.ttft_ms, 1),
                    "prompt_tokens": sample.prompt_tokens,
                    "schema_tokens": sample.schema_tokens,
                }
                for sample in self.metrics.samples
                if sample.streamed
            ],
        }


async def run_bench(
    config: Config,
    provider: LLMProvider,
    prompts: list[str] | None = None,
    *,
    warmup: bool = True,
) -> BenchReport:
    """Run `prompts` as one growing conversation and collect TTFT samples."""
    from marv.runtime.agent import Agent

    selected = list(prompts) if prompts else list(DEFAULT_PROMPTS)
    metrics = LatencyMetrics()

    # A benchmark must not land in the user's real session history.
    settings = config.to_agent_settings()
    settings.session_dir = config.session_dir

    agent = Agent(settings, provider)

    ensure = getattr(provider, "ensure_running_async", None)
    if callable(ensure):
        await ensure()

    try:
        if warmup:
            async for _ in agent.run(WARMUP_PROMPT):
                pass
            # Start measuring from a warm server and an empty slate, so the
            # cold-start cost is reported by the first sample of a normal run
            # rather than hidden inside this benchmark's median.
            agent.latency.reset()

        for prompt in selected:
            async for _ in agent.run(prompt):
                pass
            sample = agent.latency.last_sample
            if sample is not None and sample.streamed:
                # The agent reports one run's profile; the benchmark is one
                # growing conversation, so it accumulates across runs itself.
                metrics.samples.append(replace(sample))

        return BenchReport(
            provider=provider.name,
            model=provider.model,
            prompts=selected,
            metrics=metrics,
            fit=LatencyFit.from_samples(metrics.samples),
        )
    finally:
        await agent.close()


def format_report(report: BenchReport) -> str:
    """Human-readable benchmark summary."""
    metrics = report.metrics
    if metrics.turns == 0:
        return f"{report.provider} / {report.model}: no turns produced output"

    growth = metrics.ttft_growth_ms_per_turn
    if metrics.turns < 2:
        verdict = "not enough turns to measure growth"
    elif growth > 50:
        verdict = f"+{growth:.0f} ms/turn — TTFT is climbing (prompt is re-prefilled)"
    elif growth > 0:
        verdict = f"+{growth:.0f} ms/turn — mildly climbing"
    else:
        verdict = f"{growth:.0f} ms/turn — flat (prompt is not costing more per turn)"

    fit = report.fit
    calibration = (
        f"{fit.prefill_tokens_per_second:,.0f} tok/s" if fit.has_data else "not calibrated yet"
    )
    cold_start = f"{fit.cold_start_ms:.0f} ms" if fit.cold_start_ms > 0 else "not measured"

    lines = [
        f"{report.provider} / {report.model}",
        f"  turns            {metrics.turns}",
        f"  TTFT p50         {metrics.ttft_ms:.0f} ms",
        f"  TTFT p90         {metrics.ttft_p90_ms:.0f} ms",
        f"  prefill (median) {metrics.prefill_tokens:,} tokens",
        f"  prompt/turn      {metrics.prompt_tokens_per_turn:,.0f} tokens",
        f"  growth           {verdict}",
        f"  prefill rate     {calibration}",
        f"  cold start       {cold_start}",
    ]
    if fit.cache_hits:
        lines.append(f"  cache hits       {fit.cache_hits} (below the fit, kept out of the rate)")
    return "\n".join(lines)


def bench_command(
    *,
    config: Config,
    create_llm_provider: Callable[[Config], LLMProvider],
    prompts: list[str] | None = None,
    warmup: bool = True,
    as_json: bool = False,
) -> None:
    """Run the benchmark and print the report.

    Provider construction is injected so the command can be exercised without a
    live model.
    """
    provider = create_llm_provider(config)
    try:
        report = asyncio.run(run_bench(config, provider, prompts, warmup=warmup))
    finally:
        stop = getattr(provider, "stop_if_serving", None)
        if callable(stop):
            stop()

    # Seeding calibration is why ``marv bench`` exists: this machine now has
    # measured prefill constants the wait predictor can use on the next turn.
    remember_latency_fit(config.session_dir, provider.model, report.fit)

    if as_json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(format_report(report))
