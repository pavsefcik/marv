"""Chat display widget for conversation history."""

import re
from typing import TYPE_CHECKING, Any

from rich.markdown import Markdown
from rich.markup import escape
from rich.text import Text
from textual.containers import ScrollableContainer
from textual.widgets import Static

if TYPE_CHECKING:
    from marv.runtime.message import ToolCall

# Truncate long outputs for display
MAX_DISPLAY_CHARS = 5000

# Tool-row presentation glyphs. Kept here (not in CSS) because they are content.
TOOL_SUCCESS_GLYPH = "\u2713"  # check
TOOL_ERROR_GLYPH = "\u2715"  # cross
TOOL_RUNNING_GLYPH = "\u25cb"  # ring, replaced by the spinner frames below


def truncate(text: str, limit: int, message: str = "...") -> str:
    """Truncate text to limit, appending message if truncated."""
    return text if len(text) <= limit else text[:limit] + f"\n{message}"


SKILL_BLOCK_RE = re.compile(
    r'<skill\s+name="(?P<name>[^"]+)"\s+location="(?P<location>[^"]+)"\s*>'
    r"(?P<body>.*?)</skill>",
    re.DOTALL,
)


def parse_skill_block(text: str) -> tuple[str, str, str, str] | None:
    """Parse a skill block from text.

    Returns (name, location, body, remaining_text) if a top-level skill block is found.
    """
    match = SKILL_BLOCK_RE.search(text)
    if not match:
        return None

    prefix = text[: match.start()]
    if prefix.strip():
        return None

    name = match.group("name")
    location = match.group("location")
    body = match.group("body").strip()
    remaining = text[match.end() :].strip()

    return name, location, body, remaining


class MessageWidget(Static):
    """Chat message with markdown rendering."""

    def __init__(self, role: str, content: str = "", **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.role = role
        self._content = content
        self.add_class(f"message-{role}")

    def on_mount(self) -> None:
        self._update_content()

    def append_text(self, text: str) -> None:
        self._content += text
        self._update_content()

    def set_content(self, content: str) -> None:
        self._content = content
        self._update_content()

    def on_click(self) -> None:
        """Click-to-copy: terminal mouse selection is captured by the TUI."""
        if self.role == "assistant" and self._content.strip():
            self.app.copy_to_clipboard(self._content)
            self.app.notify("Copied message to clipboard")

    def text_content(self) -> str:
        """Return the current plain text content."""
        return self._content

    def _update_content(self) -> None:
        if self.role == "user":
            # User input is escaped: it must never be parsed as Textual markup.
            self.update(f"[bold $primary]> [/][bold]{escape(self._content)}[/]")
        else:
            try:
                self.update(Markdown(self._content))
            except Exception:
                # Fallback to plain text if Markdown fails
                self.update(Text(self._content))


class ToolWidget(Static):
    """Tool call display as a glyph + verb + target row with a body.

    Presentation is a single quiet row (``✓ Reading  src/parser.py``) rather
    than a bordered panel; the result is indented beneath it and can be
    collapsed. All the state methods (``set_inputs``, ``set_result``,
    ``has_result``, ``advance_spinner``, collapse/expand) are unchanged so the
    renderer and tests keep working.
    """

    SPINNER_FRAMES = [
        "\u280b",
        "\u2819",
        "\u2839",
        "\u2838",
        "\u283c",
        "\u2834",
        "\u2826",
        "\u2827",
        "\u2807",
        "\u280f",
    ]

    # Human verbs for the row. Unknown tools fall back to the raw tool name.
    VERBS = {
        "read": "Reading",
        "write": "Writing",
        "edit": "Editing",
        "bash": "Running",
        "grep": "Searching",
        "find": "Finding",
        "ls": "Listing",
    }

    def __init__(
        self, tool_id: str, name: str, inputs: dict[str, Any] | None = None, **kwargs: Any
    ) -> None:
        super().__init__(**kwargs)
        self.tool_id = tool_id
        self.tool_name = name
        self.inputs = inputs or {}
        self._result = ""
        self._is_error = False
        self._spinner_frame = 0
        self._collapsed = False
        self.add_class("tool-widget")

    def _verb(self) -> str:
        return self.VERBS.get(self.tool_name, self.tool_name)

    def _format_target(self) -> str:
        """A short, single-line summary of the tool arguments."""
        if not self.inputs:
            return ""
        # Prefer well-known path-ish arguments for a clean, familiar target.
        for key in ("path", "file_path", "pattern", "command", "query"):
            value = self.inputs.get(key)
            if isinstance(value, str) and value:
                return value if len(value) <= 60 else value[:57] + "..."
        parts = []
        for key, value in self.inputs.items():
            display = value if len(str(value)) <= 30 else "..." + str(value)[-27:]
            parts.append(f"{key}: {display}")
        summary = ", ".join(parts)
        return summary if len(summary) <= 60 else summary[:57] + "..."

    def _format_title(self) -> str:
        """Kept for compatibility: the tool name and arguments as one string."""
        if not self.inputs:
            return f"{self.tool_name}(...)"
        parts = []
        for key, value in self.inputs.items():
            if isinstance(value, str):
                display_val = value if len(value) <= 30 else "..." + value[-27:]
                parts.append(f"{key}: {display_val}")
            else:
                parts.append(f"{key}: {value}")
        return f"{self.tool_name}({', '.join(parts)})"

    def _row(self) -> str:
        """The single-line header row: glyph + verb + target (Textual markup)."""
        if self._result:
            glyph, glyph_style = (
                (TOOL_ERROR_GLYPH, "$error") if self._is_error else (TOOL_SUCCESS_GLYPH, "$success")
            )
        else:
            glyph, glyph_style = self.SPINNER_FRAMES[self._spinner_frame], "$text-muted"
        target = self._format_target()
        row = f"[{glyph_style}]{escape(glyph)}[/] [bold $primary]{escape(self._verb())}[/]"
        if target:
            row += f"  [$text-muted]{escape(target)}[/]"
        return row

    def _body(self) -> str:
        """The (possibly collapsed) result body beneath the row."""
        if not self._result:
            return ""
        if self._collapsed:
            return "[$text-muted]\u25b6 click to expand[/]"
        return f"[$text-muted]{escape(truncate(self._result, MAX_DISPLAY_CHARS))}[/]"

    def _refresh_display(self) -> None:
        row = self._row()
        body = self._body()
        self.update(f"{row}\n{body}" if body else row)

    def on_mount(self) -> None:
        self.border_title = self._format_title()
        self._refresh_display()

    def set_inputs(self, inputs: dict[str, Any]) -> None:
        """Update the tool inputs and refresh the row."""
        self.inputs = inputs
        self.border_title = self._format_title()
        self._refresh_display()

    def advance_spinner(self) -> None:
        """Advance the spinner animation."""
        if not self._result:
            self._spinner_frame = (self._spinner_frame + 1) % len(self.SPINNER_FRAMES)
            self._refresh_display()

    def set_result(self, result: str, is_error: bool = False) -> None:
        """Set the result and display it."""
        self._result = result
        self._is_error = is_error
        self._refresh_display()

    def has_result(self) -> bool:
        """Whether the tool has completed and produced output."""
        return bool(self._result)

    def collapse_output(self) -> None:
        """Collapse the tool output to show only the row."""
        if not self._collapsed and self._result:
            self._collapsed = True
            self._refresh_display()
            self.add_class("tool-collapsed")

    def expand_output(self) -> None:
        """Expand to show full result."""
        if self._collapsed and self._result:
            self._collapsed = False
            self._refresh_display()
            self.remove_class("tool-collapsed")

    def toggle(self) -> None:
        """Toggle collapsed state."""
        if self._collapsed:
            self.expand_output()
        else:
            self.collapse_output()

    def on_click(self) -> None:
        """Handle click to toggle collapsed state."""
        if self._result:
            self.toggle()


class SkillInvocationWidget(Static):
    """Collapsible skill invocation display."""

    def __init__(self, name: str, body: str, location: str | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.skill_name = name
        self.body = body
        self.location = location or ""
        self._collapsed = True
        self.add_class("skill-widget", "skill-collapsed")

    def on_mount(self) -> None:
        self._update_display()

    def _update_display(self) -> None:
        if self._collapsed:
            self.add_class("skill-collapsed")
            label = f"[skill] {self.skill_name} (click to expand)"
            self.update(Text(label))
            return

        self.remove_class("skill-collapsed")
        header = f"**[skill] {self.skill_name}**"
        if self.location:
            header += f"\n\nLocation: {self.location}"
        content = f"{header}\n\n{self.body}"
        try:
            self.update(Markdown(content))
        except Exception:
            self.update(Text(content))

    def toggle(self) -> None:
        """Toggle collapsed state."""
        self._collapsed = not self._collapsed
        self._update_display()

    def on_click(self) -> None:
        """Handle click to toggle collapsed state."""
        self.toggle()


class ThinkingWidget(Static):
    """Widget for displaying thinking/reasoning content."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._content = ""
        self._collapsed = False
        self.add_class("thinking-widget")

    def on_mount(self) -> None:
        self._update_display()

    def append_text(self, text: str) -> None:
        """Append text to thinking content."""
        self._content += text
        self._update_display()

    def text_content(self) -> str:
        """Return the current plain text content."""
        return self._content

    def _header(self) -> str:
        """The dim italic 'Thought' row that prefixes the reasoning body."""
        triangle = "\u25b8" if self._collapsed else "\u25be"
        return f"[$text-muted]{triangle}[/] [italic $text-muted]Thought[/]"

    def _update_display(self) -> None:
        """Update the display with current content."""
        display = truncate(self._content, MAX_DISPLAY_CHARS, "...[truncated]")
        header = self._header()
        if self._collapsed and display.strip():
            self.add_class("thinking-collapsed")
            self.update(f"{header}\n[$text-muted]click to expand[/]")
            return

        self.remove_class("thinking-collapsed")
        if not display.strip():
            self.update(f"{header}\n[italic $text-muted]thinking...[/]")
            return
        self.update(f"{header}\n[italic $text-muted]{escape(display)}[/]")

    def toggle(self) -> None:
        """Toggle collapsed state."""
        if self._collapsed:
            self.expand_output()
        else:
            self.collapse_output()

    def on_click(self) -> None:
        """Handle click to toggle collapsed state."""
        self.toggle()

    def collapse_output(self) -> None:
        """Collapse the thinking output to show only the title."""
        if not self._collapsed and self._content.strip():
            self._collapsed = True
            self._update_display()

    def expand_output(self) -> None:
        """Expand to show full thinking content."""
        if self._collapsed and self._content.strip():
            self._collapsed = False
            self._update_display()


class WaitingIndicator(Static):
    """Animated waiting indicator."""

    FRAMES = [
        "\u280b",
        "\u2819",
        "\u2839",
        "\u2838",
        "\u283c",
        "\u2834",
        "\u2826",
        "\u2827",
        "\u2807",
        "\u280f",
    ]
    THINKING_FRAMES = FRAMES

    def __init__(self, thinking: bool = False, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._frame = 0
        self._thinking = thinking
        self.add_class("message-assistant")

    def on_mount(self) -> None:
        self._update_display()

    def _update_display(self) -> None:
        """Update display based on current mode."""
        frames = self.THINKING_FRAMES if self._thinking else self.FRAMES
        label = "thinking" if self._thinking else "working"
        self.update(f"[$primary]{frames[self._frame]}[/] [$text-muted]{label}\u2026[/]")

    def advance(self) -> None:
        """Advance to the next animation frame."""
        self._frame = (self._frame + 1) % len(self.FRAMES)
        self._update_display()


class ChatView(ScrollableContainer):
    """Scrollable chat container."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._current_message: MessageWidget | None = None
        self._tool_widgets: dict[str, ToolWidget] = {}  # Track tools by ID
        self._current_thinking: ThinkingWidget | None = None
        self._waiting_indicator: WaitingIndicator | None = None

    def scroll_to_bottom(self) -> None:
        self.scroll_end(animate=False)

    def last_assistant_text(self) -> str | None:
        """The most recent non-empty assistant message, or None."""
        for child in reversed(self.children):
            if (
                isinstance(child, MessageWidget)
                and child.role == "assistant"
                and child.text_content().strip()
            ):
                return child.text_content()
        return None

    def add_system_message(self, text: str) -> None:
        """Add a system message."""
        self.mount(Static(f"-- {text}", classes="message-system"))
        self.scroll_to_bottom()

    def add_user_message(self, content: str) -> None:
        """Add a user message."""
        parsed = parse_skill_block(content)
        if parsed:
            name, location, body, remaining = parsed
            self.mount(SkillInvocationWidget(name, body, location))
            if remaining:
                self.mount(MessageWidget("user", remaining))
            self.scroll_to_bottom()
            return

        self.mount(MessageWidget("user", content))
        self.scroll_to_bottom()

    def add_skill_invocation(
        self,
        name: str,
        body: str,
        location: str | None = None,
        user_message: str | None = None,
    ) -> None:
        """Add a skill invocation block and optional user message."""
        self.mount(SkillInvocationWidget(name, body, location))
        if user_message:
            self.mount(MessageWidget("user", user_message))
        self.scroll_to_bottom()

    def _show_waiting(self, thinking: bool = False) -> None:
        """Show the waiting indicator."""
        if not self._waiting_indicator:
            self._waiting_indicator = WaitingIndicator(thinking=thinking)
            self.mount(self._waiting_indicator)
            self.scroll_to_bottom()

    def _hide_waiting(self) -> None:
        """Hide and remove the waiting indicator."""
        if self._waiting_indicator:
            self._waiting_indicator.remove()
            self._waiting_indicator = None

    def advance_waiting(self) -> None:
        """Advance the waiting indicator animation."""
        if self._waiting_indicator:
            self._waiting_indicator.advance()
        # Advance spinner on all incomplete tools
        for widget in self._tool_widgets.values():
            if not widget.has_result():
                widget.advance_spinner()

    def start_assistant_message(self, thinking: bool = False) -> None:
        """Start waiting for an assistant message."""
        self._show_waiting(thinking=thinking)

    async def append_to_assistant(self, text: str) -> None:
        """Append text to the current assistant message."""
        self._hide_waiting()
        self.collapse_previous_tools()
        self.collapse_previous_thinking()
        # Create message if needed (e.g., after tool results)
        if not self._current_message:
            self._current_message = MessageWidget("assistant", "")
            await self.mount(self._current_message)
        self._current_message.append_text(text)
        self.scroll_to_bottom()

    def end_assistant_message(self) -> None:
        """Finalize the current assistant message."""
        self._hide_waiting()
        self._current_message = None

    async def start_tool_call(self, tool_id: str, name: str) -> None:
        """Start a tool call widget (before arguments are ready)."""
        self._hide_waiting()
        self.collapse_previous_tools()
        self.collapse_previous_thinking()
        widget = ToolWidget(tool_id, name)
        self._tool_widgets[tool_id] = widget
        await self.mount(widget)
        self.scroll_to_bottom()

    def complete_tool_call(self, tool_call: ToolCall) -> None:
        """Complete tool call with full arguments."""
        self._hide_waiting()
        widget = self._tool_widgets.get(tool_call.id)
        if widget:
            widget.set_inputs(tool_call.arguments)
        else:
            # Create new widget if start wasn't called (fallback)
            widget = ToolWidget(tool_call.id, tool_call.name, tool_call.arguments)
            self._tool_widgets[tool_call.id] = widget
            self.mount(widget)
            self.scroll_to_bottom()

    def set_tool_result(
        self,
        tool_call_id: str,
        result: str,
        show_waiting: bool = True,
        create_if_missing: bool = False,
        is_error: bool = False,
    ) -> None:
        """Set the result on the tool widget and show waiting."""
        self._hide_waiting()
        widget = self._tool_widgets.get(tool_call_id)
        if widget:
            widget.set_result(result, is_error=is_error)
        elif create_if_missing:
            widget = ToolWidget(tool_call_id, "tool")
            self._tool_widgets[tool_call_id] = widget
            self.mount(widget)
            widget.set_result(result, is_error=is_error)
        # Show waiting indicator after tool completes
        if show_waiting:
            self._show_waiting()

    def collapse_previous_tools(self) -> None:
        """Collapse all completed tool widgets."""
        for child in self.children:
            if isinstance(child, ToolWidget) and child.has_result():
                child.collapse_output()

    def collapse_previous_thinking(self) -> None:
        """Collapse completed thinking widgets."""
        for child in self.children:
            if isinstance(child, ThinkingWidget) and child is not self._current_thinking:
                child.collapse_output()

    async def start_thinking(self) -> None:
        """Start displaying thinking content."""
        self._hide_waiting()
        if not self._current_thinking:
            self.collapse_previous_thinking()
            self._current_thinking = ThinkingWidget()
            if self._current_message and self._current_message in self.children:
                await self.mount(self._current_thinking, before=self._current_message)
            else:
                await self.mount(self._current_thinking)
            self.scroll_to_bottom()

    def append_to_thinking(self, text: str) -> None:
        """Append text to the current thinking block."""
        if self._current_thinking:
            self._current_thinking.append_text(text)
            self.scroll_to_bottom()

    def end_thinking(self) -> None:
        """Finalize the current thinking block."""
        self._current_thinking = None

    def clear_chat(self) -> None:
        """Clear all chat content."""
        self._hide_waiting()
        self._current_message = None
        self._tool_widgets.clear()
        self._current_thinking = None
        self.remove_children()
