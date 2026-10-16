"""Behavior tests for reading live runtime state from a local mlx_vlm server."""

from __future__ import annotations

import httpx
import pytest

from marv.llm.server_metrics import (
    ServerMetrics,
    ServerRequestSample,
    fetch_server_metrics,
    parse_server_metrics,
    to_latency_samples,
)

#: A realistic ``/v1/metrics`` body: a server snapshot with APC on, a couple of
#: measured requests, a junk entry, and a ``latest`` that repeats the newest
#: ``recent`` timestamp (it must not be counted twice).
PAYLOAD = {
    "server": {
        "loaded_model": "ornith-ai/Ornith-1.5-9B-MLX-4bit",
        "apc": {"enabled": True},
    },
    "latest": {
        "timestamp_unix": 1792129090.8,
        "prompt_tokens": 19,
        "completion_tokens": 5,
        "ttft_s": 0.19,
        "prefill_tok_s": 112.7,
        "decode_tok_s": 31.1,
    },
    "recent": [
        "not-a-dict",
        {
            "timestamp_unix": 1792128335.0,
            "prompt_tokens": 63,
            "completion_tokens": 37,
            "ttft_s": 0.71,
            "prefill_tok_s": 177.4,
            "decode_tok_s": 27.5,
        },
        {
            # No TTFT (never streamed): useless for a prefill-rate fit.
            "timestamp_unix": 1792129000.0,
            "prompt_tokens": 10,
            "ttft_s": 0.0,
        },
        {
            "timestamp_unix": 1792129090.8,
            "prompt_tokens": 19,
            "completion_tokens": 5,
            "ttft_s": 0.19,
            "prefill_tok_s": 112.7,
            "decode_tok_s": 31.1,
        },
    ],
}


def test_parse_extracts_apc_state_and_measured_requests() -> None:
    metrics = parse_server_metrics(PAYLOAD)

    assert metrics.apc_enabled is True
    assert [sample.prompt_tokens for sample in metrics.samples] == [63, 19]
    assert metrics.samples[0].ttft_s == pytest.approx(0.71)
    # The decode rate of the most recent request is surfaced for callers.
    assert metrics.decode_tok_s == pytest.approx(31.1)


def test_parse_of_a_body_without_apc_reports_unknown() -> None:
    metrics = parse_server_metrics(
        {"latest": {"timestamp_unix": 1.0, "prompt_tokens": 5, "ttft_s": 0.1}}
    )

    assert metrics.apc_enabled is None
    assert len(metrics.samples) == 1


def test_parse_of_junk_yields_empty_metrics() -> None:
    assert parse_server_metrics(None) == ServerMetrics()
    assert parse_server_metrics("nope") == ServerMetrics()
    assert parse_server_metrics({}).samples == []


def test_measured_requests_become_agent_latency_samples() -> None:
    metrics = parse_server_metrics(PAYLOAD)

    samples = to_latency_samples(metrics)

    assert [sample.prompt_tokens for sample in samples] == [63, 19]
    assert samples[0].ttft_ms == pytest.approx(710.0)
    assert all(sample.streamed for sample in samples)


@pytest.mark.asyncio
async def test_fetch_reads_the_server_root_metrics_endpoint() -> None:
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(200, json=PAYLOAD)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        metrics = await fetch_server_metrics(
            "http://127.0.0.1:11500/v1", client=client, timeout_s=0.5
        )

    # An OpenAI-style base URL (with /v1) still probes the server's root
    # endpoint, where mlx_vlm serves /v1/metrics.
    assert requested == ["http://127.0.0.1:11500/v1/metrics"]
    assert metrics is not None
    assert metrics.apc_enabled is True


@pytest.mark.asyncio
async def test_fetch_returns_none_when_the_endpoint_is_absent() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(404))
    ) as client:
        assert await fetch_server_metrics("http://127.0.0.1:1/v1", client=client) is None


@pytest.mark.asyncio
async def test_fetch_returns_none_when_the_server_is_unreachable() -> None:
    # Nothing listens on this port; the probe must fail fast, not hang.
    assert await fetch_server_metrics("http://127.0.0.1:9/v1", timeout_s=0.5) is None


def test_server_request_sample_defaults_are_unknown() -> None:
    sample = ServerRequestSample()

    assert sample.prompt_tokens == 0
    assert sample.ttft_s == 0.0
