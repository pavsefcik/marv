"""Behavior tests for the model menu's calibrated latency tier."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Static

from marv.config.state import remember_latency_fit
from marv.llm.latency import LatencyFit, LatencySample
from marv.runtime.settings import ThinkingLevel
from marv.tui.model_panel import ModelPanel, format_latency_tier

if TYPE_CHECKING:
    from pathlib import Path


def calibrated_fit() -> LatencyFit:
    """A fit with a known rate and cold-start constant (42 tok/s, 2.1 s)."""
    warm = LatencyFit().with_sample(
        LatencySample(ttft_ms=10_000.0, prompt_tokens=420, streamed=True)
    )
    return warm.with_sample(
        LatencySample(ttft_ms=12_100.0, prompt_tokens=420, streamed=True, cold=True)
    )


def test_format_latency_tier_shows_ttft_and_rate():
    assert format_latency_tier(calibrated_fit()) == "≈2.1s TTFT · 42 tok/s"


def test_format_latency_tier_is_empty_for_an_uncalibrated_model():
    """Never invent a number: no sample means no tier."""
    assert format_latency_tier(LatencyFit()) == ""


def test_format_latency_tier_without_a_cold_sample_shows_only_the_rate():
    fit = LatencyFit().with_sample(
        LatencySample(ttft_ms=10_000.0, prompt_tokens=420, streamed=True)
    )

    assert format_latency_tier(fit) == "42 tok/s"


class _AgentFake:
    """Minimal agent surface the model menu reads."""

    def __init__(self, session_dir: Path, model: str) -> None:
        self.config = type("Config", (), {"session_dir": session_dir})()
        self.model_name = model
        self.provider_name = "marv-mlx"
        self.thinking_level = ThinkingLevel.OFF


class _PanelApp(App[None]):
    """Host an inline panel so it mounts and renders for real."""

    def __init__(self, panel: ModelPanel) -> None:
        super().__init__()
        self._panel = panel

    def compose(self) -> ComposeResult:
        yield self._panel


@pytest.mark.asyncio
async def test_model_panel_info_shows_a_recorded_calibration(temp_dir):
    """A model with a persisted fit shows its measured tier in the info line."""
    session_dir = temp_dir / "sessions"
    remember_latency_fit(session_dir, "mlx-community/Calibrated-4bit", calibrated_fit())
    agent = _AgentFake(session_dir, "mlx-community/Calibrated-4bit")

    app = _PanelApp(ModelPanel(agent, ["mlx-community/Calibrated-4bit"]))  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        await pilot.pause()

        info = app.query_one("#model-info", Static)
        text = _plain(info)

    assert "≈2.1s TTFT" in text
    assert "42 tok/s" in text


@pytest.mark.asyncio
async def test_model_panel_info_omits_the_tier_when_uncalibrated(temp_dir):
    """An uncalibrated model must show no latency number at all."""
    session_dir = temp_dir / "sessions"
    agent = _AgentFake(session_dir, "mlx-community/Unknown-4bit")

    app = _PanelApp(ModelPanel(agent, ["mlx-community/Unknown-4bit"]))  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        await pilot.pause()

        info = app.query_one("#model-info", Static)
        text = _plain(info)

    assert "tok/s" not in text
    assert "TTFT" not in text
    assert "reasoning:" in text


def _plain(widget: Static) -> str:
    renderable: Any = widget.render()
    return getattr(renderable, "plain", str(renderable))
