"""Behavior tests for the quiet, terminal-native TUI presentation.

These cover the observable presentation contract: tool rows show a state
glyph, verb and target; thinking is a dim italic row; the startup header is
compact; answers can be selected; and user input can never be interpreted as
markup.
"""

from __future__ import annotations

import re

import pytest
from textual.color import Color
from textual.geometry import Offset
from textual.selection import Selection
from textual.widgets import Static, TextArea

from marv.config import Config
from marv.runtime.session import Session
from marv.tui.app import AgentApp
from marv.tui.chat import (
    TOOL_ERROR_GLYPH,
    TOOL_SUCCESS_GLYPH,
    ChatView,
    MessageWidget,
    StatusNotice,
    ThinkingWidget,
    ToolWidget,
)
from marv.tui.renderer import banner_text
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


@pytest.mark.asyncio
async def test_user_message_carries_a_primary_stripe(temp_dir):
    """A prompt is marked by an opaque stripe, not by a background.

    The ANSI themes resolve `$surface`/`$panel` to transparent, so a
    background could not separate a prompt from an answer there; a border
    drawn in `$primary` can, and it keeps the message at full width.
    """
    app = make_app(temp_dir)

    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        chat = app.query_one("#chat-view", ChatView)

        message = MessageWidget("user", "fix the parser")
        await chat.mount(message)
        await pilot.pause()

        border_type, border_colour = message.styles.border_left
        assert border_type != ""
        assert border_colour == Color.parse(app.get_css_variables()["primary"])
        assert border_colour.a == 1
        assert message.styles.background.a == 0
        assert message.outer_size.width == chat.content_size.width


@pytest.mark.asyncio
async def test_an_answer_is_extractable_as_plain_text(temp_dir):
    """A selection over a message yields its text, not a ``RichVisual`` repr.

    Answers render from Markdown, and Textual's default extraction stringifies
    the visual, so the selected text of a reply was a repr and could not be
    copied at all.
    """
    app = make_app(temp_dir)

    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        chat = app.query_one("#chat-view", ChatView)

        prompt = MessageWidget("user", "fix the parser")
        answer = MessageWidget("assistant", "the parser fix is in src/parser.py")
        await chat.mount(prompt)
        await chat.mount(answer)
        await pilot.pause()

        # Offset coordinates are (x, y) and are read per line against the
        # widget's own content: cells 0-14 of the answer's only line.
        app.screen.selections = {answer: Selection(Offset(0, 0), Offset(14, 0))}
        section = app.screen.get_selected_text()
        assert section == "the parser fix"

        app.screen.selections = {answer: Selection(Offset(0, 0), Offset(36, 0))}
        whole_line = app.screen.get_selected_text()
        assert whole_line == "the parser fix is in src/parser.py"

        # A user prompt is selectable too, `> ` marker included.
        app.screen.selections = {prompt: Selection(Offset(0, 0), Offset(30, 0))}
        asked = app.screen.get_selected_text()
        assert asked == "> fix the parser"

        # ctrl+c copies the selection — the section, not the whole reply —
        # rather than quitting marv (the quit binding applies to no selection).
        app.screen.selections = {answer: Selection(Offset(0, 0), Offset(14, 0))}
        await pilot.press("ctrl+c")
        await pilot.pause()
        assert app.clipboard == "the parser fix"
        assert app.is_running

        app.screen.clear_selection()
        await pilot.press("ctrl+c")
        await pilot.pause()
        assert not app.is_running


@pytest.mark.asyncio
async def test_clicking_an_answer_selects_text_instead_of_copying_it(temp_dir, monkeypatch):
    """Answers are extractable: a click must not hijack the clipboard.

    Click-to-copy made mouse text selection useless for answers, because any
    click copied the whole reply. Selection is Textual's again, and `ctrl+o`
    stays the whole-reply fallback.
    """
    copied: list[str] = []
    monkeypatch.setattr("textual.app.App.copy_to_clipboard", lambda self, text: copied.append(text))
    app = make_app(temp_dir)

    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        chat = app.query_one("#chat-view", ChatView)

        answer = MessageWidget("assistant", "the parser fix is in src/parser.py")
        await chat.mount(answer)
        await pilot.pause()

        await pilot.click(answer)
        await pilot.pause()
        assert copied == []

        # A drag over one section copies that section, not the whole message.
        await pilot.mouse_down(answer, offset=(0, 0))
        await pilot.mouse_up(answer, offset=(28, 0))
        await pilot.pause()
        selected = app.screen.get_selected_text()
        assert selected is not None
        assert "the parser fix" in selected

        app.action_copy_last_reply()
        assert copied == ["the parser fix is in src/parser.py"]


@pytest.mark.asyncio
async def test_tool_and_thinking_rows_still_toggle_on_a_click(temp_dir):
    """The rows keep their click-to-collapse affordance."""
    app = make_app(temp_dir)

    async with app.run_test() as pilot:
        await pilot.pause()
        chat = app.query_one("#chat-view", ChatView)

        tool = ToolWidget("t1", "read", {"path": "src/parser.py"})
        await chat.mount(tool)
        tool.set_result("contents")
        thinking = ThinkingWidget()
        await chat.mount(thinking)
        thinking.append_text("Weighing the fix")
        await pilot.pause()

        await pilot.click(tool)
        await pilot.click(thinking)
        await pilot.pause()
        assert "tool-collapsed" in tool.classes
        assert "thinking-collapsed" in thinking.classes

        await pilot.click(tool)
        await pilot.pause()
        assert "tool-collapsed" not in tool.classes


def test_startup_hint_advertises_only_bindings_that_exist():
    """The hint names controls that are really bound, and names the useful ones."""
    line = banner_text("gpt-4o", "off").splitlines()[1]
    hint = re.sub(r"\[[^\]]*\]", "", line)
    advertised = set(re.findall(r"ctrl\+\w+|escape", hint))

    expected = "Type /help for commands · escape interrupt · ctrl+o copy reply"
    assert hint == expected
    assert advertised == {"escape", "ctrl+o"}
    assert advertised <= {binding.key for binding in AgentApp.BINDINGS}


@pytest.mark.asyncio
async def test_prompt_placeholder_uses_the_themes_disabled_text_colour(temp_dir):
    """The placeholder is muted, and the override actually wins.

    A `>` combinator never matches a component class, so the rule was dead and
    TextArea's own default (`$text 40%`) applied instead. The selector is a
    descendant one now: the assertion below is the cascade, not the file.
    """
    app = make_app(temp_dir)

    async with app.run_test() as pilot:
        await pilot.pause()
        prompt = app.query_one("#prompt-inner", TextArea)

        placeholder = prompt.get_component_styles("text-area--placeholder").color
        typed = prompt.styles.color

        assert placeholder != typed
        assert placeholder.a < 1
        assert any(
            rule.selectors == "#prompt-inner .text-area--placeholder"
            for rule in app.stylesheet.rules
        )


@pytest.mark.asyncio
async def test_transient_status_notice_leads_with_the_success_glyph(temp_dir):
    """A settled notice is an outcome (`✓ model ready`), not a `--` line."""
    app = make_app(temp_dir)

    async with app.run_test() as pilot:
        await pilot.pause()
        chat = app.query_one("#chat-view", ChatView)

        chat.set_notice("model ready")
        await pilot.pause()
        notice = chat.query_one(StatusNotice)
        assert render_text(notice) == f"{TOOL_SUCCESS_GLYPH} model ready"

        # The waiting form is a spinner row and stays as it was.
        notice.set_status("starting qwen", spinner=True)
        await pilot.pause()
        waiting = render_text(notice)
        assert TOOL_SUCCESS_GLYPH not in waiting
        assert waiting.endswith(" starting qwen")

        # Historical system messages keep the `--` marker.
        chat.add_system_message("interrupted")
        await pilot.pause()
        assert any(
            render_text(widget) == "-- interrupted"
            for widget in chat.query(Static)
            if "message-system" in widget.classes
        )
