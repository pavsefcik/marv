"""Behavior tests for the latency benchmark."""

from __future__ import annotations

import json

import pytest

from marv.cli.bench import BenchReport, format_report, run_bench
from marv.config import Config
from tests.test_doubles.llm_provider_fake import LLMProviderFake
from tests.test_doubles.llm_stream_builders import make_text_events


def build_config(temp_dir) -> Config:
    return Config(
        provider="openai",
        model="fake-model",
        api_key="test",
        session_dir=temp_dir,
        context_max_tokens=2048,
        max_output_tokens=2048,
    )


@pytest.mark.asyncio
async def test_bench_runs_every_prompt_as_one_conversation(temp_dir):
    """Each prompt is measured; the conversation grows, so the prompt grows."""
    prompts = ["one", "two", "three"]
    provider = LLMProviderFake([make_text_events(f"reply {i}") for i in range(len(prompts))])

    report = await run_bench(build_config(temp_dir), provider, prompts, warmup=False)

    assert report.model == "fake-model"
    assert report.prompts == prompts
    assert report.metrics.turns == len(prompts)
    # Each turn carries the accumulated history, so prompt cost rises.
    assert report.metrics.prompt_tokens_per_turn > 0


@pytest.mark.asyncio
async def test_bench_warmup_turn_is_excluded_from_the_report(temp_dir):
    """Warm-up warms the server; it must not pollute the measured samples."""
    prompts = ["one", "two"]
    provider = LLMProviderFake([make_text_events(f"reply {i}") for i in range(len(prompts) + 1)])

    report = await run_bench(build_config(temp_dir), provider, prompts, warmup=True)

    assert report.metrics.turns == len(prompts)
    # warm-up + one call per prompt
    assert len(provider.stream_calls) == len(prompts) + 1


def test_format_report_flags_climbing_ttft(temp_dir):
    """A climbing TTFT is the headline diagnostic and must be called out."""
    from marv.llm.latency import LatencySample

    report = BenchReport(provider="fake", model="m", prompts=["a", "b", "c"])
    report.metrics.samples = [
        LatencySample(ttft_ms=100.0, prompt_tokens=100, streamed=True),
        LatencySample(ttft_ms=300.0, prompt_tokens=200, streamed=True),
        LatencySample(ttft_ms=500.0, prompt_tokens=300, streamed=True),
    ]

    text = format_report(report)

    assert "TTFT p50" in text
    assert "climbing" in text


def test_format_report_flags_flat_ttft(temp_dir):
    from marv.llm.latency import LatencySample

    report = BenchReport(provider="fake", model="m", prompts=["a", "b"])
    report.metrics.samples = [
        LatencySample(ttft_ms=200.0, prompt_tokens=100, streamed=True),
        LatencySample(ttft_ms=200.0, prompt_tokens=100, streamed=True),
    ]

    assert "flat" in format_report(report)


def test_report_serializes_to_json():
    from marv.llm.latency import LatencySample

    report = BenchReport(provider="fake", model="m", prompts=["a"])
    report.metrics.samples = [LatencySample(ttft_ms=123.4, prompt_tokens=500, streamed=True)]

    payload = json.loads(json.dumps(report.to_dict()))

    assert payload["provider"] == "fake"
    assert payload["turns"] == 1
    assert payload["ttft_ms"]["p50"] == 123.4
    assert payload["samples"][0]["prompt_tokens"] == 500


def test_format_report_handles_no_turns():
    report = BenchReport(provider="fake", model="m")

    assert "no turns produced output" in format_report(report)


def test_bench_cli_wires_options_into_the_report(temp_dir, monkeypatch):
    """The command is reachable and forwards its prompts/model to the bench."""
    from typer.testing import CliRunner

    from marv import cli

    monkeypatch.chdir(temp_dir)
    captured: dict[str, object] = {}

    def fake_bench_command(*, config, create_llm_provider, prompts, warmup, as_json):
        captured["prompts"] = prompts
        captured["warmup"] = warmup
        captured["as_json"] = as_json
        captured["model"] = config.model

    monkeypatch.setattr("marv.cli.bench.bench_command", fake_bench_command)

    result = CliRunner().invoke(
        cli.app,
        ["bench", "--prompt", "alpha", "--prompt", "beta", "--no-warmup", "--json", "-m", "chosen"],
    )

    assert result.exit_code == 0
    assert captured["prompts"] == ["alpha", "beta"]
    assert captured["warmup"] is False
    assert captured["as_json"] is True
    assert captured["model"] == "chosen"


def test_bench_cli_rejects_zero_turns(temp_dir, monkeypatch):
    from typer.testing import CliRunner

    from marv import cli

    monkeypatch.chdir(temp_dir)

    result = CliRunner().invoke(cli.app, ["bench", "--turns", "0"])

    assert result.exit_code == 1
    assert "--turns must be at least 1" in result.output
