"""Context menu — context information docked at the input line."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.text import Text
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.widgets import Static

from marv.tui.panel import DockPanel

if TYPE_CHECKING:
    from textual.app import ComposeResult

    from marv.runtime.agent import Agent


class ContextPanel(DockPanel[None]):
    """Context viewer with summary/messages/system tabs.

    Left/right switch tabs, up/down scroll, escape returns to input mode.
    """

    _TABS = ("summary", "messages", "system")

    BINDINGS = [
        Binding("escape", "cancel", "Close", show=False),
        Binding("left", "prev_tab", "Previous tab", show=False),
        Binding("right", "next_tab", "Next tab", show=False),
        Binding("up", "line_up", "Scroll up", show=False),
        Binding("down", "line_down", "Scroll down", show=False),
        Binding("pageup", "page_up", "Page up", show=False),
        Binding("pagedown", "page_down", "Page down", show=False),
        Binding("home", "top", "Top", show=False),
        Binding("end", "bottom", "Bottom", show=False),
    ]

    def __init__(self, agent: Agent) -> None:
        super().__init__()
        self._agent = agent
        self._tab_index = 0

    def compose(self) -> ComposeResult:
        yield Static(self._tab_header(), id="context-tabs", classes="panel-title")
        with VerticalScroll(id="context-scroll"):
            yield Static(self._build_summary(), id="summary-content")
            yield Static(self._build_messages(), id="messages-content")
            yield Static(self._build_system(), id="system-content")
        yield Static("← → tabs · ↑ ↓ scroll · esc to close", classes="panel-hint")

    def on_mount(self) -> None:
        self._show_tab(0)
        self.focus_target()

    # ---- tabs -----------------------------------------------------------

    def _tab_header(self) -> Text:
        text = Text()
        for index, name in enumerate(self._TABS):
            if index:
                text.append("  ·  ", style="dim")
            text.append(name, style="bold" if index == self._tab_index else "dim")
        return text

    def _show_tab(self, index: int) -> None:
        self._tab_index = index % len(self._TABS)
        names = ("summary-content", "messages-content", "system-content")
        for position, name in enumerate(names):
            self.query_one(f"#{name}", Static).display = position == self._tab_index
        self.query_one("#context-tabs", Static).update(self._tab_header())
        self.query_one("#context-scroll", VerticalScroll).scroll_home(animate=False)

    def action_prev_tab(self) -> None:
        """Show the previous tab."""
        self._show_tab(self._tab_index - 1)

    def action_next_tab(self) -> None:
        """Show the next tab."""
        self._show_tab(self._tab_index + 1)

    # ---- scrolling ------------------------------------------------------

    def action_line_up(self) -> None:
        self.query_one("#context-scroll", VerticalScroll).action_scroll_up()

    def action_line_down(self) -> None:
        self.query_one("#context-scroll", VerticalScroll).action_scroll_down()

    def action_page_up(self) -> None:
        self.query_one("#context-scroll", VerticalScroll).action_page_up()

    def action_page_down(self) -> None:
        self.query_one("#context-scroll", VerticalScroll).action_page_down()

    def action_top(self) -> None:
        self.query_one("#context-scroll", VerticalScroll).action_scroll_home()

    def action_bottom(self) -> None:
        self.query_one("#context-scroll", VerticalScroll).action_scroll_end()

    # ---- content --------------------------------------------------------

    def _build_summary(self) -> str:
        """Build the summary tab content."""
        lines = []

        # Model section
        lines.append("[bold cyan]MODEL[/]")
        lines.append(f"  Provider: {self._agent.provider_name}")
        lines.append(f"  Model: {self._agent.model_name}")
        lines.append(f"  Thinking: {self._agent.thinking_level.value}")
        lines.append(f"  Temperature: {self._agent.temperature}")
        lines.append(f"  Max Output Tokens: {self._agent.max_output_tokens}")
        lines.append("")

        # Token usage
        lines.append("[bold cyan]TOKEN USAGE[/]")
        current_tokens = self._agent.total_tokens
        context_max_tokens = self._agent.context_max_tokens
        reserve = self._agent.context_reserve_tokens
        pct = (current_tokens / context_max_tokens * 100) if context_max_tokens > 0 else 0
        available = context_max_tokens - reserve
        lines.append(f"  Current: {current_tokens:,} / {context_max_tokens:,} ({pct:.1f}%)")
        lines.append(f"  Reserve: {reserve:,} (for response + tools)")
        lines.append(f"  Available: {available:,}")
        lines.append("")

        # Session info
        lines.append("[bold cyan]SESSION[/]")
        session = self._agent.session
        lines.append(f"  ID: {session.metadata.id}")
        if session.metadata.parent_session_id:
            lines.append(f"  Parent: {session.metadata.parent_session_id}")
        if session.leaf_id:
            lines.append(f"  Leaf: {session.leaf_id}")
        lines.append(f"  Created: {session.metadata.created_at.strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append(f"  Working dir: {session.metadata.cwd}")
        lines.append(f"  File: {session.path}")
        lines.append("")

        # Message counts
        messages = session.messages
        total = len(messages)
        by_role: dict[str, int] = {}
        for msg in messages:
            role = msg.role.value
            by_role[role] = by_role.get(role, 0) + 1

        lines.append(f"[bold cyan]MESSAGES[/] ({total} total)")
        for role in ["system", "user", "assistant", "tool"]:
            if role in by_role:
                lines.append(f"  {role.capitalize()}: {by_role[role]}")
        lines.append("")

        # Tools
        tools = self._agent.list_tools()
        lines.append(f"[bold cyan]TOOLS[/] ({len(tools)})")
        lines.append(f"  {', '.join(sorted(tools))}")
        lines.append("")

        # Skills
        skills = self._agent.list_skills()
        lines.append(f"[bold cyan]SKILLS[/] ({len(skills)})")
        if skills:
            for skill in sorted(skills, key=lambda s: s.name):
                lines.append(f"  {skill.name} [dim]({skill.source.value})[/]")
        else:
            lines.append("  [dim](none)[/]")
        lines.append("")

        # Templates
        templates = self._agent.list_templates()
        lines.append(f"[bold cyan]TEMPLATES[/] ({len(templates)})")
        if templates:
            for tmpl in sorted(templates, key=lambda t: t.name):
                lines.append(f"  {tmpl.name} [dim]({tmpl.source.value})[/]")
        else:
            lines.append("  [dim](none)[/]")
        lines.append("")

        # Context files
        ctx_files = self._agent.context_files
        lines.append(f"[bold cyan]CONTEXT FILES[/] ({len(ctx_files)})")
        if ctx_files:
            for cf in ctx_files:
                if cf.source == "ancestor":
                    lines.append(f"  {cf.path.name} [dim](ancestor: {cf.path.parent})[/]")
                else:
                    lines.append(f"  {cf.path.name} [dim]({cf.source})[/]")
        else:
            lines.append("  [dim](none)[/]")

        return "\n".join(lines)

    def _build_messages(self) -> Text:
        """Build the messages tab content.

        Returns a Text object to safely handle user content without markup parsing.
        """
        messages = self._agent.session.messages
        result = Text()

        for i, msg in enumerate(messages):
            role_upper = msg.role.value.upper()

            # Color code by role
            role_colors = {
                "SYSTEM": "yellow",
                "USER": "green",
                "ASSISTANT": "blue",
                "TOOL": "magenta",
            }
            color = role_colors.get(role_upper, "white")

            # Header with markup
            result.append_text(Text.from_markup(f"[bold {color}]━━━ [{i}] {role_upper} ━━━[/]\n"))

            # Show tool_call_id for tool messages
            if msg.tool_call_id:
                result.append_text(Text.from_markup(f"[dim]tool_call_id: {msg.tool_call_id}[/]\n"))

            # Show content as plain text (no markup parsing)
            content = msg.content
            if len(content) > 3000:
                result.append(content[:3000])  # Plain text, no markup
                result.append_text(
                    Text.from_markup(f"\n[dim]... truncated, {len(msg.content):,} chars total[/]")
                )
            else:
                result.append(content)  # Plain text, no markup
            result.append("\n")

            # Show tool calls if present
            if msg.tool_calls:
                result.append_text(Text.from_markup("\n[bold]Tool calls:[/]\n"))
                for tc in msg.tool_calls:
                    result.append_text(
                        Text.from_markup(f"  [cyan]{tc.name}[/] [dim]({tc.id})[/]\n")
                    )
                    args_str = str(tc.arguments)
                    if len(args_str) > 200:
                        args_str = args_str[:200] + "..."
                    result.append("    ")
                    result.append(args_str, style="dim")  # Plain text with dim style
                    result.append("\n")

            # Show thinking if present
            if msg.thinking and msg.thinking.text:
                thinking_preview = msg.thinking.text[:300]
                if len(msg.thinking.text) > 300:
                    thinking_preview += "..."
                result.append("\n")
                result.append("Thinking: " + thinking_preview, style="dim italic")
                result.append("\n")

            result.append("\n")

        result.append_text(Text.from_markup(f"[bold]Total: {len(messages)} messages[/]"))
        return result

    def _build_system(self) -> Text:
        """Build the system prompt tab content.

        Returns a Text object to safely handle system prompt content without markup parsing.
        """
        messages = self._agent.session.messages
        system_msgs = [m for m in messages if m.role.value == "system"]
        result = Text()

        if system_msgs:
            for i, msg in enumerate(system_msgs):
                if i > 0:
                    result.append_text(Text.from_markup("\n[dim]" + "─" * 50 + "[/]\n\n"))
                result.append(msg.content)  # Plain text, no markup parsing

            result.append_text(Text.from_markup("\n\n[dim]" + "─" * 50 + "[/]\n"))
            total_chars = sum(len(m.content) for m in system_msgs)
            result.append_text(
                Text.from_markup(
                    f"[bold]Total: {len(system_msgs)} system message(s), {total_chars:,} chars[/]"
                )
            )
        else:
            result.append_text(Text.from_markup("[dim](no system prompt)[/]"))

        return result
