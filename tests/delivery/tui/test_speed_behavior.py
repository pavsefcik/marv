"""Behavior tests for the live tokens-per-second estimate."""

from __future__ import annotations

import pytest

from marv.tui.speed import TokenSpeedTracker


def count_chars(text: str) -> int:
    return len(text)


def test_rate_is_not_reported_before_a_sample_interval(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr("marv.tui.speed.time.monotonic", lambda: clock[0])
    tracker = TokenSpeedTracker(count_chars)

    assert tracker.add("one two") is None  # anchors the window
    clock[0] = 0.2
    assert tracker.add("three") is None  # too soon to sample
    assert tracker.rate == 0


def test_rate_reports_tokens_per_second_once_the_window_elapses(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr("marv.tui.speed.time.monotonic", lambda: clock[0])
    tracker = TokenSpeedTracker(count_chars)

    tracker.add("a b")  # anchor: 3 characters
    clock[0] = 1.0
    rate = tracker.add("cdefgh")  # 6 more characters in 1.0s

    assert rate == pytest.approx(6.0)
    assert tracker.rate == pytest.approx(6.0)


def test_reset_does_not_count_idle_time_before_the_next_generation(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr("marv.tui.speed.time.monotonic", lambda: clock[0])
    tracker = TokenSpeedTracker(count_chars)

    tracker.add("a b")
    clock[0] = 1.0
    tracker.add("cd")  # samples 2 characters/s

    tracker.reset()
    clock[0] = 60.0  # a minute of thinking/tool time later
    tracker.add("xyzw")  # new window anchors here, idle time excluded
    clock[0] = 61.0
    tracker.add("abcd")

    assert tracker.rate == pytest.approx(4.0)


@pytest.mark.asyncio
async def test_status_bar_shows_the_generation_speed(temp_dir):
    from marv.config import Config
    from marv.runtime.session import Session
    from marv.tui.app import AgentApp
    from marv.tui.status import StatusBar
    from tests.test_doubles.llm_provider_fake import LLMProviderFake

    app = AgentApp(
        Config(provider="openai", model="gpt-4o", api_key="test", session_dir=temp_dir),
        provider=LLMProviderFake([], name="openai", model="gpt-4o"),
        session=Session.new(temp_dir),
    )

    async with app.run_test() as pilot:
        await pilot.pause()

        app.query_one("#status-line", StatusBar).set_speed(42.4)
        left = app.query_one("#status-left").render()
        text = getattr(left, "plain", str(left))
        assert "42 tok/s" in text
