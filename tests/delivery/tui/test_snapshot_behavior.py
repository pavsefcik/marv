"""Screen snapshot guardrails for the quiet TUI presentation.

These pin the whole rendered frame — layout, glyphs, colours, and the
relationships between rows — so the visual redesign cannot silently regress.
They complement the focused widget assertions in ``test_presentation_behavior.py``.

Snapshots are committed text frames under ``snapshots/``; update them with
``MARV_UPDATE_SNAPSHOTS=1 make test`` after reviewing the diff.
"""

from __future__ import annotations

import os

import pytest

from marv.config import Config
from marv.runtime.session import Session
from marv.tui.app import AgentApp
from marv.tui.chat import ChatView, ThinkingWidget, ToolWidget
from tests.delivery.tui.snapshot_support import (
    assert_matches_snapshot,
    snapshot_styled,
    snapshot_text,
)
from tests.test_doubles.llm_provider_fake import LLMProviderFake

# Fixed so the status bar's cwd (and its padding) is identical on every machine.
FIXED_CWD = "/workspace/marv"


@pytest.fixture(autouse=True)
def _deterministic_environment(monkeypatch):
    """Keep frames reproducible across machines.

    The RAM reading comes from host helpers (`sysctl`/`ps`/`vm_stat`) and the
    status bar shows the live working directory; both are stubbed at that
    boundary so the frame does not depend on the test machine.
    """
    import marv.tui.app as app_module

    monkeypatch.setattr(app_module, "snapshot", lambda server_pid=None: None)
    monkeypatch.setattr(os, "getcwd", lambda: FIXED_CWD)


def make_app(temp_dir, **overrides) -> AgentApp:
    values = {
        "provider": "openai",
        "model": "gpt-4o",
        "api_key": "test",
        # The `minimal` theme resolves to concrete colours, so styled snapshots
        # are stable without depending on the host terminal's palette.
        "theme": "minimal",
        "session_dir": temp_dir,
    }
    values.update(overrides)
    return AgentApp(
        Config(**values),
        provider=LLMProviderFake([], name="openai", model="gpt-4o"),
        session=Session.new(temp_dir),
    )


@pytest.mark.asyncio
async def test_startup_screen_snapshot(temp_dir):
    app = make_app(temp_dir)

    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()

        assert_matches_snapshot("startup", snapshot_text(app))


@pytest.mark.asyncio
async def test_successful_tool_row_snapshot(temp_dir):
    app = make_app(temp_dir)

    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        chat = app.query_one("#chat-view", ChatView)

        tool = ToolWidget("t1", "read", {"path": "src/parser.py"})
        await chat.mount(tool)
        tool.set_result("def parse(source):\n    return source")
        await pilot.pause()

        assert_matches_snapshot("tool-success", snapshot_text(app))


@pytest.mark.asyncio
async def test_failed_tool_row_snapshot(temp_dir):
    app = make_app(temp_dir)

    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        chat = app.query_one("#chat-view", ChatView)

        tool = ToolWidget("t1", "edit", {"path": "src/nope.py"})
        await chat.mount(tool)
        tool.set_result("Error: String not found", is_error=True)
        await pilot.pause()

        # Styled: the ✕ glyph and judgement-bearing verb styling are part of the
        # contract, and plain text cannot express them.
        assert_matches_snapshot("tool-error-styled", snapshot_styled(app))


@pytest.mark.asyncio
async def test_thinking_row_snapshot(temp_dir):
    app = make_app(temp_dir)

    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        chat = app.query_one("#chat-view", ChatView)

        thinking = ThinkingWidget()
        await chat.mount(thinking)
        thinking.append_text("Weighing the parser fix before editing")
        await pilot.pause()

        # Styled: "dim italic" is the whole point of this row.
        assert_matches_snapshot("thinking-styled", snapshot_styled(app))


@pytest.mark.asyncio
async def test_light_theme_is_applied_from_terminal_appearance(temp_dir, monkeypatch):
    from marv.tui import theme as theme_module

    theme_module.reset_appearance_cache()
    monkeypatch.setattr(theme_module, "_query_terminal_appearance", lambda: False)
    monkeypatch.setattr(theme_module, "_system_appearance", lambda: None)
    app = make_app(temp_dir, theme="auto")

    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()

        # The ANSI themes render identically to text, so the theme name (not a
        # text snapshot) is what actually pins this behaviour.
        assert app.theme == "ansi-light"
