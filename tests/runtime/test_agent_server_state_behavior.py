"""Behavior tests for the agent reading live server state for its wait prediction."""

from __future__ import annotations

import pytest

from marv.config import Config
from marv.llm.latency import LatencyFit, LatencySample
from marv.llm.server_metrics import ServerMetrics, ServerRequestSample
from marv.runtime.agent import Agent
from marv.runtime.chunk import WaitEstimateChunk
from tests.test_doubles.llm_provider_fake import LLMProviderFake
from tests.test_doubles.llm_stream_builders import make_text_events


class ServerReportingProviderFake(LLMProviderFake):
    """Fake local provider whose server reports live metrics."""

    def __init__(self, metrics: ServerMetrics | None, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._metrics = metrics

    async def server_metrics(self) -> ServerMetrics | None:
        return self._metrics


def build_agent_with_server(
    temp_dir,
    metrics: ServerMetrics | None,
    scripts: list[list[object]],
) -> tuple[Agent, ServerReportingProviderFake]:
    config = Config(
        provider="openai",
        model="fake-model",
        api_key="test",
        session_dir=temp_dir,
        context_max_tokens=2048,
        max_output_tokens=2048,
    )
    fake = ServerReportingProviderFake(
        metrics,
        scripts=scripts,
        name="fake",
        model="fake-model",  # type: ignore[arg-type]
    )
    return Agent(config.to_agent_settings(), fake), fake


def _metrics(apc: bool = True) -> ServerMetrics:
    return ServerMetrics(
        apc_enabled=apc,
        samples=[
            ServerRequestSample(prompt_tokens=2000, ttft_s=1.0),
            ServerRequestSample(prompt_tokens=4000, ttft_s=2.0),
        ],
    )


async def _estimates(agent: Agent, prompt: str) -> list[WaitEstimateChunk]:
    return [chunk async for chunk in agent.run(prompt) if isinstance(chunk, WaitEstimateChunk)]


@pytest.mark.asyncio
async def test_server_measurements_seed_the_prediction_before_the_first_turn(temp_dir):
    """A first-ever run must get an ETA, from the server's own measured turns."""

    agent, _ = build_agent_with_server(temp_dir, _metrics(), [make_text_events("Hello")])

    estimates = await _estimates(agent, "Hi")

    assert len(estimates) == 1
    assert estimates[0].payload.eta_ms is not None
    assert agent.latency_fit.prefill_tokens_per_second == pytest.approx(2000.0)


@pytest.mark.asyncio
async def test_an_unreachable_server_leaves_the_prediction_unseeded(temp_dir):
    """No metrics endpoint means no fabricated numbers, exactly as before."""

    agent, _ = build_agent_with_server(temp_dir, None, [make_text_events("Hello")])

    estimates = await _estimates(agent, "Hi")

    assert len(estimates) == 1
    assert estimates[0].payload.eta_ms is None
    assert not agent.latency_fit.has_data


@pytest.mark.asyncio
async def test_a_persisted_calibration_is_not_overwritten_by_server_samples(temp_dir):
    """The machine's own measured turns win over the server's coarse history."""

    agent, _ = build_agent_with_server(temp_dir, _metrics(), [make_text_events("Hello")])
    persisted = LatencyFit.from_samples(
        [LatencySample(prompt_tokens=1000, ttft_ms=100.0, streamed=True)]
    )
    agent.set_latency_fit(persisted)

    await _estimates(agent, "Hi")

    assert agent.latency_fit is persisted


@pytest.mark.asyncio
async def test_an_apc_server_makes_the_next_turn_expect_a_prefix_cache_hit(temp_dir):
    """With APC on, turn two's prediction prices only the newly added tokens."""

    agent, _ = build_agent_with_server(
        temp_dir,
        _metrics(apc=True),
        [make_text_events("Hello world, this is a long answer"), make_text_events("More")],
    )

    first = await _estimates(agent, "first question")
    second = await _estimates(agent, "second question")

    assert len(first) == 1 and len(second) == 1
    # Turn one has no cached prefix to expect; turn two expects the previous
    # turn's full footprint (prompt + schemas + completion) as a cache hit.
    assert first[0].payload.cached_tokens == 0
    assert second[0].payload.cached_tokens > 0
    assert second[0].payload.cached_tokens <= second[0].payload.prefill_tokens
    assert second[0].payload.eta_ms is not None
    assert first[0].payload.eta_ms is not None
    # Pricing only the suffix must not predict a longer wait than the full
    # prompt at the same rate.
    assert second[0].payload.eta_ms <= first[0].payload.eta_ms


@pytest.mark.asyncio
async def test_a_server_without_apc_never_expects_cached_tokens(temp_dir):
    """No prefix cache server-side means every token is priced as prefill."""

    agent, _ = build_agent_with_server(
        temp_dir,
        _metrics(apc=False),
        [make_text_events("Hello"), make_text_events("More")],
    )

    await _estimates(agent, "first question")
    second = await _estimates(agent, "second question")

    assert second[0].payload.cached_tokens == 0
