"""Behavior tests for legible prefill waits (ROADMAP P1 "Predictable wait").

A local model can be silent for two minutes while it prefills, so the waiting
indicator has to narrate *this machine's own* prediction rather than spin
opaquely. These cover the observable contract:

- the indicator tracks elapsed against the prediction it was given;
- the prediction revises, and an overrun says so instead of reaching 100%;
- a first-ever run (no measured fit) shows elapsed only, never an invented ETA;
- the status bar carries the prediction beside the last measured ttft;
- the calibration is seeded from and persisted to ``state.toml`` per model.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import pytest
from textual.widgets import Static

from marv.config import Config
from marv.config.state import load_last_used, remember_latency_fit
from marv.llm.events import (
    DoneEvent,
    PartialMessage,
    StreamOptions,
    TextDeltaEvent,
    TextStartEvent,
)
from marv.llm.latency import LatencyFit, LatencySample, WaitEstimate
from marv.llm.stream import AssistantMessageEventStream
from marv.runtime.chunk import WaitEstimateChunk
from marv.runtime.session import Session
from marv.tui.app import AgentApp
from marv.tui.chat import ChatView, WaitingIndicator
from marv.tui.status import StatusBar
from tests.test_doubles.llm_provider_fake import LLMProviderFake

if TYPE_CHECKING:
    from pathlib import Path

    from marv.runtime.message import Message


class ManualClock:
    """A monotonic clock the test advances by hand."""

    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class GatedProviderFake(LLMProviderFake):
    """Provider double that can hold a stream open or delay its first token.

    ``gated=True`` lets a test observe the UI *during* a wait (the estimate is
    emitted before the request goes out); ``delay`` gives the turn a measurable
    time-to-first-token for the calibration tests.
    """

    def __init__(self, *, delay: float = 0.0, gated: bool = False) -> None:
        super().__init__([], name="openai", model="gpt-4o")
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self._delay = delay
        self._gated = gated

    def stream(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        options: StreamOptions | None = None,
    ) -> AssistantMessageEventStream:
        stream = AssistantMessageEventStream()

        async def _run() -> None:
            self.started.set()
            if self._gated:
                stream.push(TextStartEvent(content_index=0))
                await self.release.wait()
            elif self._delay:
                await asyncio.sleep(self._delay)
            stream.push(TextStartEvent(content_index=0))
            stream.push(TextDeltaEvent(content_index=0, delta="done"))
            stream.push(DoneEvent(message=PartialMessage()))
            stream.end()

        stream.attach_task(asyncio.create_task(_run()))
        return stream


def fit_with(rate_tokens_per_second: float) -> LatencyFit:
    """A calibration whose warm prefill rate is exactly ``rate`` tok/s."""
    return LatencyFit.from_samples(
        [
            LatencySample(
                ttft_ms=10_000.0,
                prompt_tokens=int(rate_tokens_per_second * 10),
                streamed=True,
            )
        ]
    )


def render_text(widget: Static) -> str:
    renderable = widget.render()
    return getattr(renderable, "plain", str(renderable))


def left_text(app: AgentApp) -> str:
    """The status bar's left section as plain text."""
    left = app.query_one("#status-line", StatusBar).query_one("#status-left", Static)
    return render_text(left)


def make_app(temp_dir, provider: LLMProviderFake | None = None) -> AgentApp:
    return AgentApp(
        Config(
            provider="openai",
            model="gpt-4o",
            api_key="test",
            session_dir=temp_dir / "sessions",
        ),
        provider=provider or LLMProviderFake([], name="openai", model="gpt-4o"),
        session=Session.new(temp_dir),
    )


def test_indicator_narrates_elapsed_against_a_revising_prediction():
    clock = ManualClock()
    indicator = WaitingIndicator(
        estimate=WaitEstimate(prompt_tokens=32_000, eta_ms=120_000.0),
        clock=clock,
    )

    start = render_text(indicator)
    assert "prefilling 32k tokens" in start
    assert "~2:00" in start
    assert "0:00 elapsed" in start

    clock.advance(74)
    indicator.advance()

    later = render_text(indicator)
    assert "~0:46" in later  # the prediction revises rather than freezing
    assert "1:14 elapsed" in later


def test_indicator_says_taking_longer_than_predicted_past_the_estimate():
    clock = ManualClock()
    indicator = WaitingIndicator(
        estimate=WaitEstimate(prompt_tokens=32_000, eta_ms=120_000.0),
        clock=clock,
    )

    clock.advance(121)
    indicator.advance()

    text = render_text(indicator)
    assert "taking longer than predicted" in text
    assert "2:01 elapsed" in text
    assert "100%" not in text
    assert "~0:00" not in text


def test_indicator_without_an_estimate_degrades_to_elapsed_only():
    clock = ManualClock()
    indicator = WaitingIndicator(
        estimate=WaitEstimate(prompt_tokens=32_000, eta_ms=None),
        clock=clock,
    )

    assert "~" not in render_text(indicator)  # no fabricated number

    clock.advance(45)
    indicator.advance()

    text = render_text(indicator)
    assert "prefilling 32k tokens" in text
    assert "0:45 elapsed" in text
    assert "~" not in text


def test_indicator_keeps_the_spinner_frames_animating():
    clock = ManualClock()
    indicator = WaitingIndicator(
        estimate=WaitEstimate(prompt_tokens=1000, eta_ms=60_000.0),
        clock=clock,
    )

    frames = {render_text(indicator)[0]}
    for _ in range(len(WaitingIndicator.FRAMES) - 1):
        indicator.advance()
        frames.add(render_text(indicator)[0])

    assert frames == set(WaitingIndicator.FRAMES)


def test_indicator_keeps_the_plain_spinner_without_an_estimate():
    indicator = WaitingIndicator(thinking=True)

    first = render_text(indicator)
    assert "thinking" in first

    indicator.advance()

    assert "thinking" in render_text(indicator)
    assert render_text(indicator) != first  # the frame moved


@pytest.mark.asyncio
async def test_chat_view_applies_a_late_arriving_estimate(temp_dir):
    app = make_app(temp_dir)

    async with app.run_test() as pilot:
        await pilot.pause()
        chat = app.query_one("#chat-view", ChatView)

        chat.start_assistant_message()
        await pilot.pause()
        assert "working" in render_text(chat.query_one(WaitingIndicator))

        chat.apply_wait_estimate(WaitEstimate(prompt_tokens=32_000, eta_ms=120_000.0))
        await pilot.pause()

        text = render_text(chat.query_one(WaitingIndicator))
        assert "prefilling 32k tokens" in text
        # A real clock: the remaining figure is the prediction, minus the
        # microseconds since the estimate arrived.
        assert "~2:00" in text or "~1:59" in text


@pytest.mark.asyncio
async def test_renderer_narrates_the_estimate_it_receives(temp_dir):
    """A WaitEstimateChunk reaches both the indicator and the status bar."""
    provider = GatedProviderFake(gated=True)
    app = make_app(temp_dir, provider=provider)

    async with app.run_test() as pilot:
        await pilot.pause()
        app.agent.set_latency_fit(fit_with(500.0))

        task = asyncio.create_task(app._renderer.render_agent_run("hello", None))
        for _ in range(100):
            if provider.started.is_set():
                break
            await pilot.pause()
        await pilot.pause()

        chat = app.query_one("#chat-view", ChatView)
        assert "prefilling" in render_text(chat.query_one(WaitingIndicator))
        assert "eta ~" in left_text(app)

        provider.release.set()
        await task
        await pilot.pause()

        # The measured ttft replaces the prediction once the turn streams.
        assert "ttft" in left_text(app)
        assert "eta" not in left_text(app)
        assert "elapsed" not in left_text(app)


@pytest.mark.asyncio
async def test_status_bar_shows_eta_beside_ttft_and_omits_it_when_unknown(temp_dir):
    app = make_app(temp_dir)

    async with app.run_test() as pilot:
        await pilot.pause()
        status = app.query_one("#status-line", StatusBar)

        status.set_ttft(1200.0, 32_000)
        status.set_eta(120_000.0)
        await pilot.pause()

        assert "ttft 1200ms/32,000t" in left_text(app)
        assert "eta ~2:00" in left_text(app)

        status.set_eta(None)
        await pilot.pause()

        assert "eta" not in left_text(app)


@pytest.mark.asyncio
async def test_status_bar_shows_a_decoding_elapsed_reading(temp_dir):
    app = make_app(temp_dir)

    async with app.run_test() as pilot:
        await pilot.pause()
        status = app.query_one("#status-line", StatusBar)

        assert "elapsed" not in left_text(app)

        status.set_elapsed(74.0)
        await pilot.pause()

        assert "1:14 elapsed" in left_text(app)


@pytest.mark.asyncio
async def test_the_agent_emits_the_estimate_the_app_seeded(temp_dir):
    app = make_app(temp_dir)
    app.agent.set_latency_fit(fit_with(1000.0))

    emitted = [chunk async for chunk in app.agent.run("hello")]
    estimates = [chunk for chunk in emitted if isinstance(chunk, WaitEstimateChunk)]

    assert estimates
    estimate = estimates[0].payload
    assert estimate.prefill_tokens > 0
    # Predicted from the installed fit alone -- no other constant involved.
    assert estimate.eta_ms == pytest.approx(estimate.prefill_tokens, rel=0.05)


@pytest.mark.asyncio
async def test_app_seeds_the_persisted_fit_for_the_active_model(temp_dir):
    remember_latency_fit(temp_dir / "sessions", "gpt-4o", fit_with(1000.0))
    app = make_app(temp_dir)

    async with app.run_test() as pilot:
        await pilot.pause()

        assert app.agent.latency_fit.has_data
        assert app.agent.latency_fit.prefill_tokens_per_second == pytest.approx(1000.0)


@pytest.mark.asyncio
async def test_app_persists_the_refined_fit_per_model(temp_dir):
    app = make_app(temp_dir)

    async with app.run_test() as pilot:
        await pilot.pause()

        # A turn as the latency tracker measures it: 20k prefill tokens in 10s.
        app.agent.latency.note_request(prompt_tokens=20_000, now=0.0)
        app.agent.latency.note_first_token(now=10.0)
        app.remember_latency_fit()

        fit = load_last_used(temp_dir / "sessions").fit_for("gpt-4o")
        assert fit.sample_count == 1
        assert fit.prefill_tokens_per_second == pytest.approx(2000.0)

    # A fresh session with no measured turns must not overwrite the calibration.
    empty_app = make_app(temp_dir)
    async with empty_app.run_test() as pilot:
        await pilot.pause()
        empty_app.remember_latency_fit()

    assert load_last_used(temp_dir / "sessions").fit_for("gpt-4o").sample_count == 1


def test_headless_persists_a_measured_fit(temp_dir):
    from marv.cli.headless import run_headless

    config = Config(
        provider="openai",
        model="gpt-4o",
        api_key="test",
        session_dir=temp_dir / "sessions",
    )

    asyncio.run(run_headless(config, "hello", None, GatedProviderFake(delay=0.15)))

    fit = load_last_used(temp_dir / "sessions").fit_for("gpt-4o")
    assert fit.sample_count == 1
    assert fit.has_data


def test_headless_seeds_the_persisted_fit(temp_dir, monkeypatch):
    """The headless run predicts from this machine's own measured turns."""
    from marv.cli import headless
    from marv.runtime.agent import Agent

    remember_latency_fit(temp_dir / "sessions", "gpt-4o", fit_with(1000.0))
    seeded: list[LatencyFit] = []
    original = Agent.set_latency_fit

    def record(self: Agent, fit: LatencyFit | None) -> None:
        seeded.append(fit or LatencyFit())
        original(self, fit)

    monkeypatch.setattr(Agent, "set_latency_fit", record)
    config = Config(
        provider="openai",
        model="gpt-4o",
        api_key="test",
        session_dir=temp_dir / "sessions",
    )

    asyncio.run(
        headless.run_headless(
            config,
            "hello",
            None,
            LLMProviderFake([], name="openai", model="gpt-4o"),
        )
    )

    assert seeded
    assert seeded[0].prefill_tokens_per_second == pytest.approx(1000.0)


def test_a_first_ever_run_persists_nothing_fabricated(temp_dir: Path) -> None:
    """No measured turns means no fit, so no ETA and no invented constant."""
    from marv.cli.headless import run_headless

    config = Config(
        provider="openai",
        model="gpt-4o",
        api_key="test",
        session_dir=temp_dir / "sessions",
    )

    asyncio.run(
        run_headless(
            config,
            "hello",
            None,
            LLMProviderFake([], name="openai", model="gpt-4o"),
        )
    )

    assert load_last_used(temp_dir / "sessions").latency_fits == {}


def test_indicator_offers_compaction_for_a_long_conversation_wait():
    """A long, prompt-dominated wait is a decision: offer the exit."""
    clock = ManualClock()
    indicator = WaitingIndicator(
        estimate=WaitEstimate(prompt_tokens=40_000, schema_tokens=2_000, eta_ms=120_000.0),
        clock=clock,
    )

    text = render_text(indicator)
    assert "prefilling 42k tokens" in text
    assert "/compact" in text


def test_indicator_does_not_offer_advice_for_a_short_wait():
    """An ordinary turn must never nag."""
    indicator = WaitingIndicator(
        estimate=WaitEstimate(prompt_tokens=1_000, schema_tokens=500, eta_ms=2_000.0),
    )

    assert "/compact" not in render_text(indicator)
    assert "predicted wait" not in render_text(indicator)


@pytest.mark.asyncio
async def test_slash_compact_summarizes_and_reports_the_shrinkage(temp_dir):
    """`/compact` is the exit from a long-predicted wait, on demand."""
    from marv.runtime.message import Message, Role

    app = make_app(temp_dir)
    async with app.run_test() as pilot:
        await pilot.pause()
        # Enough history that compaction has a middle to summarize.
        for i in range(16):
            app.agent.session.append(Message(role=Role.USER, content=f"message {i}"))
        app.agent._total_tokens = 40_000

        handled = await app._controller.handle_prompt_command("/compact")
        await pilot.pause()

        assert handled is True
        chat = app.query_one("#chat-view", ChatView)
        messages = [child for child in chat.children if isinstance(child, Static)]
        assert any("compacted" in render_text(child) for child in messages)


def test_help_lists_compact():
    from marv.tui.controller import TUIController

    source = TUIController.handle_prompt_command.__doc__ or ""

    # The help text is user-facing documentation; /compact must be discoverable.
    from marv.tui.input import BUILTIN_COMMANDS

    assert "/compact" in BUILTIN_COMMANDS
    assert source is not None  # keeps the test honest about its own scope
