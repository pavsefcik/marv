"""Behavior tests for the quiet, terminal-native TUI presentation.

These cover the observable presentation contract: tool rows show a state
glyph, verb and target; thinking is a dim italic row; the startup header is
compact; answers can be selected; and user input can never be interpreted as
markup.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import pytest
from textual.color import Color
from textual.widgets import Label, Markdown, Static, TextArea
from textual.widgets.markdown import MarkdownFence

from marv.config import Config
from marv.runtime.message import ImageAttachment
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

if TYPE_CHECKING:
    from textual.pilot import Pilot


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


# An answer long enough to wrap over several lines of an 80-column chat, so a
# drag can start on its first line and finish several lines further down.
WRAPPING_ANSWER = (
    "The parser fix is in src/parser.py: the tokenizer now keeps the trailing "
    "comma so a list of arguments still parses when the last one is empty and "
    "the caller passes no keywords at all, which used to raise a confusing "
    "TypeError from deep inside the resolver."
)

# Two one-line paragraphs, one blank line apart, so each rendered line's cells
# are known exactly.
PARAGRAPHS = "the parser fix is in src/parser.py\n\nand the second line follows here"


async def drag(
    pilot: Pilot, widget: MessageWidget, start: tuple[int, int], end: tuple[int, int]
) -> str:
    """Drag the mouse over ``widget`` and return the text that got selected.

    Offsets are (x, y) cells relative to the widget's own region; a message
    indents its body by two cells, so (2, 0) is the body's first cell.
    """
    await pilot.mouse_down(widget, offset=start)
    await pilot.mouse_up(widget, offset=end)
    await pilot.pause()
    return pilot.app.screen.get_selected_text() or ""


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
async def test_a_drag_over_an_answer_selects_only_the_dragged_text(temp_dir):
    """A drag inside an answer selects what it swept over, not the whole reply.

    An answer used to render as a Rich ``Markdown`` renderable, which carries no
    content offsets, so Textual had no cell offset to anchor a selection to and
    took its select-all path: every drag copied the entire reply.
    """
    app = make_app(temp_dir)

    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        chat = app.query_one("#chat-view", ChatView)

        answer = MessageWidget("assistant", WRAPPING_ANSWER)
        await chat.mount(answer)
        await pilot.pause()

        # The body's first cell down to eleven cells into its fourth line:
        # three full wrapped lines plus eleven cells, and nothing beyond them.
        selected = await drag(pilot, answer, (2, 0), (12, 3))

        assert selected == WRAPPING_ANSWER[:227]
        assert selected != WRAPPING_ANSWER

        # ctrl+c copies the selection — the dragged part, not the whole reply —
        # rather than quitting marv (the quit binding applies to no selection).
        await pilot.press("ctrl+c")
        await pilot.pause()
        assert app.clipboard == selected
        assert app.is_running

        app.screen.clear_selection()
        await pilot.press("ctrl+c")
        await pilot.pause()
        assert not app.is_running


@pytest.mark.asyncio
async def test_a_drag_selects_an_answer_line_and_nothing_beyond_it(temp_dir):
    """A drag past the end of a line stops at the line, not at the answer."""
    app = make_app(temp_dir)

    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        chat = app.query_one("#chat-view", ChatView)

        answer = MessageWidget("assistant", PARAGRAPHS)
        await chat.mount(answer)
        await pilot.pause()

        selected = await drag(pilot, answer, (2, 0), (72, 0))
        assert selected == "the parser fix is in src/parser.py"


@pytest.mark.asyncio
async def test_a_drag_across_answer_lines_starts_where_the_drag_did(temp_dir):
    """A drag spanning two answer lines reads from the cell it started on."""
    app = make_app(temp_dir)

    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        chat = app.query_one("#chat-view", ChatView)

        answer = MessageWidget("assistant", PARAGRAPHS)
        await chat.mount(answer)
        await pilot.pause()

        # From four cells into the first paragraph to ten cells into the second.
        selected = await drag(pilot, answer, (6, 0), (12, 2))
        assert selected == "parser fix is in src/parser.py\nand the se"


@pytest.mark.asyncio
async def test_a_user_prompt_is_selectable(temp_dir):
    """A prompt is selectable too: the `> ` marker and the image lines."""
    app = make_app(temp_dir)

    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        chat = app.query_one("#chat-view", ChatView)

        attachment = ImageAttachment(media_type="image/png", data="", name="shot.png")
        prompt = MessageWidget("user", "fix the parser", [attachment])
        await chat.mount(prompt)
        await pilot.pause()

        assert await drag(pilot, prompt, (2, 0), (9, 0)) == "> fix th"
        app.screen.clear_selection()
        assert await drag(pilot, prompt, (2, 0), (17, 0)) == "> fix the parser"
        app.screen.clear_selection()
        assert await drag(pilot, prompt, (2, 1), (20, 1)) == "  [image: shot.png]"


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
        selected = await drag(pilot, answer, (2, 0), (15, 0))
        assert selected == "the parser fix"

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
async def test_prompt_placeholder_is_grey_under_the_ansi_themes(temp_dir):
    """ANSI has no alpha, so `$text-disabled` collapses to the terminal's
    default foreground (white). The `:ansi` rule pins the placeholder to bright
    black, so a prompt is still visibly grey on a stock terminal."""
    app = make_app(temp_dir, theme="ansi-dark")

    async with app.run_test() as pilot:
        await pilot.pause()
        prompt = app.query_one("#prompt-inner", TextArea)

        placeholder = prompt.get_component_styles("text-area--placeholder").color
        assert placeholder.ansi == 8  # ansi_bright_black


@pytest.mark.asyncio
async def test_streaming_appends_keep_the_raw_text_and_render_it(temp_dir):
    """An answer built by streamed deltas holds the raw text and shows it."""
    app = make_app(temp_dir)

    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        chat = app.query_one("#chat-view", ChatView)

        answer = MessageWidget("assistant")
        await chat.mount(answer)
        answer.append_text("the parser fix ")
        answer.append_text("is in **src/parser.py**")
        await pilot.pause()

        assert answer.text_content() == "the parser fix is in **src/parser.py**"
        block = answer.query_one(Markdown).query_one("MarkdownParagraph")
        assert block.render().plain == "the parser fix is in src/parser.py"

        # The whole text is replaced, not appended to the rendered body.
        answer.set_content("the tokenizer fix")
        await pilot.pause()
        assert answer.text_content() == "the tokenizer fix"
        blocks = list(answer.query_one(Markdown).children)
        assert [child.render().plain for child in blocks] == ["the tokenizer fix"]


@pytest.mark.asyncio
async def test_an_answer_still_renders_markdown(temp_dir):
    """Headings, lists, inline code and bold survive the move to Markdown blocks."""
    app = make_app(temp_dir)

    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        chat = app.query_one("#chat-view", ChatView)

        answer = MessageWidget(
            "assistant", "# Heading\n\n- one\n- two\n\nthe `tokenizer` is **wrong**"
        )
        await chat.mount(answer)
        await pilot.pause()

        blocks = list(answer.query_one(Markdown).children)
        assert [child.name for child in blocks] == [
            "heading_open",
            "bullet_list_open",
            "paragraph_open",
        ]
        assert blocks[0].render().plain == "Heading"

        paragraph = blocks[-1]
        styles = [span.style for span in paragraph.render().spans if isinstance(span.style, str)]
        assert ".code_inline" in styles
        assert ".strong" in styles


@pytest.mark.asyncio
async def test_a_code_fence_frames_the_code_without_a_scrollbar_row_below_it(temp_dir):
    """The frame surrounds the code; no black scrollbar sits underneath it."""
    app = make_app(temp_dir)

    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        chat = app.query_one("#chat-view", ChatView)

        answer = MessageWidget("assistant", "```python\nprint(1)\n```")
        await chat.mount(answer)
        await pilot.pause()

        fence = answer.query_one(MarkdownFence)
        label = fence.query_one(Label)
        assert fence.scrollbar_size_horizontal == 0
        assert fence.region.height == label.region.height
        default_margin = fence.styles.margin
        default_padding = label.styles.padding

        # ANSI themes have no alpha for Textual's `black 10%` frame, so marv
        # supplies an opaque one instead of leaving the code unframed. They also
        # collapse Textual's fence margin/padding, which made code blocks (and
        # the headings around them) sit differently from every hex theme.
        app.theme = "ansi-dark"
        await pilot.pause()
        assert fence.styles.background.a > 0
        assert fence.styles.margin == default_margin
        assert label.styles.padding == default_padding


@pytest.mark.asyncio
async def test_transient_status_notice_leads_with_the_success_glyph(temp_dir):
    """A settled notice is an outcome (`✓ all systems ready`), not a `--` line."""
    app = make_app(temp_dir)

    async with app.run_test() as pilot:
        await pilot.pause()
        chat = app.query_one("#chat-view", ChatView)

        chat.set_notice("all systems ready")
        await pilot.pause()
        notice = chat.query_one(StatusNotice)
        assert render_text(notice) == f"{TOOL_SUCCESS_GLYPH} all systems ready"

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


@pytest.mark.asyncio
async def test_system_message_content_is_shown_literally(temp_dir):
    """System text is data, not markup: shell output cannot inject Rich tags."""
    app = make_app(temp_dir)

    async with app.run_test() as pilot:
        await pilot.pause()
        chat = app.query_one("#chat-view", ChatView)

        chat.add_system_message("$ echo [bold red]hi[/]")
        await pilot.pause()

        rendered = [
            render_text(widget)
            for widget in chat.query(Static)
            if "message-system" in widget.classes
        ]
        assert any("$ echo [bold red]hi[/]" in text for text in rendered)
