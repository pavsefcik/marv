"""Behavior tests for the quiet, terminal-native TUI presentation.

These cover the observable presentation contract: tool rows show a state
glyph, verb and target; thinking is a dim italic row; the startup header is
compact; and user input can never be interpreted as markup.
"""

from __future__ import annotations

import pytest
from textual.widgets import Static

from marv.config import Config
from marv.runtime.session import Session
from marv.tui.app import AgentApp
from marv.tui.chat import (
    TOOL_ERROR_GLYPH,
    TOOL_SUCCESS_GLYPH,
    ChatView,
    MessageWidget,
    ThinkingWidget,
    ToolWidget,
)
from tests.test_doubles.llm_provider_fake import LLMProviderFake


def make_app(temp_dir, **overrides) -> AgentApp:
    values = {
        "provider": "openai",
        "model": "gpt-4o",
        "api_key": "test",
        "session_dir": temp_dir,
    }
    values.update(overrides)
    return AgentApp(
        Config(**values),
        provider=LLMProviderFake([], name="openai", model="gpt-4o"),
        session=Session.new(temp_dir),
    )


def render_text(widget: Static) -> str:
    renderable = widget.render()
    return getattr(renderable, "plain", str(renderable))


@pytest.mark.asyncio
async def test_tool_row_shows_a_success_glyph_and_a_human_verb(temp_dir):
    app = make_app(temp_dir)

    async with app.run_test() as pilot:
        await pilot.pause()
        chat = app.query_one("#chat-view", ChatView)

        tool = ToolWidget("t1", "read", {"path": "src/parser.py"})
        await chat.mount(tool)
        tool.set_result("contents")
        await pilot.pause()

        text = render_text(tool)
        assert TOOL_SUCCESS_GLYPH in text
        assert "Reading" in text
        assert "src/parser.py" in text
        assert "contents" in text


@pytest.mark.asyncio
async def test_tool_row_shows_an_error_glyph_for_a_failed_tool(temp_dir):
    app = make_app(temp_dir)

    async with app.run_test() as pilot:
        await pilot.pause()
        chat = app.query_one("#chat-view", ChatView)

        tool = ToolWidget("t1", "edit", {"path": "src/nope.py"})
        await chat.mount(tool)
        tool.set_result("Error: String not found", is_error=True)
        await pilot.pause()

        text = render_text(tool)
        assert TOOL_ERROR_GLYPH in text
        assert TOOL_SUCCESS_GLYPH not in text


@pytest.mark.asyncio
async def test_tool_row_shows_no_glyph_until_the_tool_finishes(temp_dir):
    app = make_app(temp_dir)

    async with app.run_test() as pilot:
        await pilot.pause()
        chat = app.query_one("#chat-view", ChatView)

        tool = ToolWidget("t1", "bash", {"command": "uv run pytest"})
        await chat.mount(tool)
        await pilot.pause()

        text = render_text(tool)
        assert TOOL_SUCCESS_GLYPH not in text
        assert TOOL_ERROR_GLYPH not in text
        assert "Running" in text


@pytest.mark.asyncio
async def test_thinking_is_a_dim_italic_row_with_a_disclosure_triangle(temp_dir):
    app = make_app(temp_dir)

    async with app.run_test() as pilot:
        await pilot.pause()
        chat = app.query_one("#chat-view", ChatView)

        thinking = ThinkingWidget()
        await chat.mount(thinking)
        thinking.append_text("Weighing the parser fix")
        await pilot.pause()

        expanded = render_text(thinking)
        assert "Thought" in expanded
        assert "Weighing the parser fix" in expanded
        assert "\u25be" in expanded  # expanded triangle

        thinking.collapse_output()
        await pilot.pause()

        collapsed = render_text(thinking)
        assert "\u25b8" in collapsed  # collapsed triangle
        assert "Weighing the parser fix" not in collapsed


@pytest.mark.asyncio
async def test_startup_header_is_compact_and_names_the_model(temp_dir):
    app = make_app(temp_dir)

    async with app.run_test() as pilot:
        await pilot.pause()
        chat = app.query_one("#chat-view", ChatView)

        headers = [
            render_text(widget)
            for widget in chat.query(Static)
            if "message-system" in widget.classes
        ]
        header = next(text for text in headers if "marv" in text)

        assert "gpt-4o" in header
        assert "marv" in header
        # The old ASCII-art banner must be gone.
        assert "\u2588" not in header


@pytest.mark.asyncio
async def test_user_input_is_never_parsed_as_markup(temp_dir):
    app = make_app(temp_dir)

    async with app.run_test() as pilot:
        await pilot.pause()
        chat = app.query_one("#chat-view", ChatView)

        message = MessageWidget("user", "[bold $primary]not markup[/]")
        await chat.mount(message)
        await pilot.pause()

        assert message.text_content() == "[bold $primary]not markup[/]"
        assert "[bold $primary]not markup[/]" in render_text(message)
