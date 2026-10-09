"""Behavior tests for the latency benchmark."""

from __future__ import annotations

import asyncio
import json

import pytest

from marv.cli.bench import BenchReport, bench_command, format_report, run_bench
from marv.config import Config
from marv.config.state import load_last_used
from marv.llm.events import DoneEvent, PartialMessage
from marv.llm.latency import LatencyFit, LatencySample
from marv.llm.stream import AssistantMessageEventStream
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


class SlowFirstTokenProvider(LLMProviderFake):
    """Fake provider whose first token lands after a measurable delay.

    The plain fake streams instantly, so its TTFT is below the calibration's
    floor and no fit can be learned. Returning a scripted stream after a real
    sleep is what makes ``run_bench``'s fit derivation observable.
    """

    def __init__(self, delay_seconds: float, **kwargs) -> None:
        super().__init__(**kwargs)
        self._delay_seconds = delay_seconds

    def stream(self, messages, tools=None, options=None):
        self.stream_calls.append({"messages": list(messages), "tools": tools, "options": options})
        events = self._scripts.pop(0) if self._scripts else make_text_events("")
        stream = AssistantMessageEventStream()

        async def _run() -> None:
            await asyncio.sleep(self._delay_seconds)
            for event in events:
                stream.push(event)
                await asyncio.sleep(0)
            if not any(e.type in ("done", "error") for e in events):
                stream.push(DoneEvent(message=PartialMessage()))
            stream.end()

        stream.attach_task(asyncio.create_task(_run()))
        return stream


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
    report = BenchReport(provider="fake", model="m", prompts=["a", "b"])
    report.metrics.samples = [
        LatencySample(ttft_ms=200.0, prompt_tokens=100, streamed=True),
        LatencySample(ttft_ms=200.0, prompt_tokens=100, streamed=True),
    ]

    assert "flat" in format_report(report)


def test_report_serializes_to_json():
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


def calibrated_fit() -> LatencyFit:
    """A fit with a known rate and cold-start constant (42 tok/s, 2.1 s)."""
    warm = LatencyFit().with_sample(
        LatencySample(ttft_ms=10_000.0, prompt_tokens=420, streamed=True)
    )
    return warm.with_sample(
        LatencySample(ttft_ms=12_100.0, prompt_tokens=420, streamed=True, cold=True)
    )


@pytest.mark.asyncio
async def test_bench_learns_a_calibration_from_its_own_samples(temp_dir):
    """The report carries the prefill calibration implied by the measured turns."""
    prompts = ["one", "two"]
    provider = SlowFirstTokenProvider(
        0.06, scripts=[make_text_events(f"reply {i}") for i in range(len(prompts))]
    )

    report = await run_bench(build_config(temp_dir), provider, prompts, warmup=False)

    assert report.metrics.turns == len(prompts)
    # Every measured sample informs the fit this machine will predict from.
    assert report.fit.sample_count == len(prompts)
    assert report.fit.has_data
    assert report.fit.prefill_tokens_per_second == pytest.approx(
        LatencyFit.from_samples(report.metrics.samples).prefill_tokens_per_second
    )


def test_format_report_shows_the_learned_prefill_rate(temp_dir):
    """The calibration is reported, not just measured."""
    report = BenchReport(provider="fake", model="m", prompts=["a", "b"])
    report.metrics.samples = [
        LatencySample(ttft_ms=200.0, prompt_tokens=100, streamed=True),
        LatencySample(ttft_ms=200.0, prompt_tokens=100, streamed=True),
    ]
    report.fit = calibrated_fit()

    text = format_report(report)

    assert "42 tok/s" in text
    assert "2100 ms" in text


def test_format_report_says_so_when_a_model_is_uncalibrated():
    """No measured turns must not be dressed up as a number."""
    report = BenchReport(provider="fake", model="m", prompts=["a"])
    report.metrics.samples = [LatencySample(ttft_ms=200.0, prompt_tokens=100, streamed=True)]

    text = format_report(report)

    assert "not calibrated yet" in text
    assert "tok/s" not in text


def test_report_serializes_the_calibration():
    report = BenchReport(provider="fake", model="m", prompts=["a"])
    report.metrics.samples = [LatencySample(ttft_ms=200.0, prompt_tokens=100, streamed=True)]
    report.fit = calibrated_fit()

    payload = json.loads(json.dumps(report.to_dict()))

    assert payload["calibration"]["prefill_tokens_per_second"] == 42.0
    assert payload["calibration"]["cold_start_ms"] == 2100.0
    assert payload["calibration"]["cold_samples"] == 1


def test_bench_command_seeds_the_calibration_in_state(temp_dir, monkeypatch):
    """`marv bench` is how a fresh machine learns its prefill constants."""
    config = build_config(temp_dir)
    provider = LLMProviderFake(model="fake-model")
    fit = calibrated_fit()

    async def fake_run_bench(*args, **kwargs):
        return BenchReport(provider=provider.name, model=provider.model, fit=fit)

    monkeypatch.setattr("marv.cli.bench.run_bench", fake_run_bench)

    bench_command(config=config, create_llm_provider=lambda _config: provider, prompts=["a"])

    persisted = load_last_used(config.session_dir).fit_for("fake-model")
    assert persisted.has_data
    assert persisted.prefill_tokens_per_second == pytest.approx(42.0)
    assert persisted.cold_start_ms == pytest.approx(2100.0)
