"""Theme menu — pick the TUI palette from the input line.

Highlighting an entry applies it immediately, so the whole screen is the
preview. Escape restores the palette that was active when the menu opened; Enter
keeps the choice and the app persists it to ``state.toml``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from rich.text import Text
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from marv.tui.panel import DockPanel, separator_option
from marv.tui.theme import (
    AUTO_THEME,
    MARV_MLX_THEME_NAME,
    MINIMAL_THEME_NAME,
    available_theme_names,
    resolve_theme_name_safely,
)

if TYPE_CHECKING:
    from textual.app import ComposeResult

    from marv.tui.app import AgentApp

#: What the highlighted entry means, for the info line.
_DESCRIPTIONS = {
    AUTO_THEME: "match this terminal (dark or light)",
    MARV_MLX_THEME_NAME: "the marv family palette (magenta)",
    MINIMAL_THEME_NAME: "the old Nord-ish palette",
}

#: Entries before this index are marv's own; the rest are built-in Textual
#: themes, so the divider lands between the two groups.
_MARV_PRESETS = 3


class ThemePanel(DockPanel[str]):
    """Menu for switching the TUI theme, with live preview.

    Dismisses with the *configured* value (``auto`` rather than the concrete
    ``ansi-dark``/``ansi-light`` it resolves to), so the terminal-matching
    choice survives a restart.
    """

    def __init__(self, configured: str) -> None:
        super().__init__()
        self._configured = configured
        #: The concrete theme to restore if the menu is cancelled.
        self._original = ""
        #: Suppresses preview events until the menu is fully built.
        self._shown = False

    def compose(self) -> ComposeResult:
        yield Static("theme", classes="panel-title")
        yield Static("", id="theme-subtitle", classes="panel-subtitle")
        yield OptionList(id="panel-options", classes="panel-menu")
        yield Static("", id="theme-info", classes="panel-subtitle")
        yield Static("↑/↓ nav · enter set · esc back", id="theme-hint", classes="panel-hint")

    def on_mount(self) -> None:
        self._original = self._app().theme
        self._show()
        self.focus_target()

    def focus_target(self) -> None:
        """Focus the list, highlighting the active theme."""
        option_list = self.query_one(OptionList)
        option_list.focus()
        for index in range(option_list.option_count):
            if option_list.get_option_at_index(index).id == self._configured:
                option_list.highlighted = index
                break

    def _app(self) -> AgentApp:
        return cast("AgentApp", self.app)

    def _show(self) -> None:
        resolved = resolve_theme_name_safely(self._configured)
        self.query_one("#theme-subtitle", Static).update(
            Text(f"current: {self._configured} ({resolved})")
        )

        options: list[Option] = []
        for index, name in enumerate(available_theme_names()):
            if index == _MARV_PRESETS:
                options.append(separator_option())
            label = f"{name}  // current" if name == self._configured else name
            options.append(Option(Text(label), id=name))

        option_list = self.query_one(OptionList)
        option_list.clear_options()
        option_list.add_options(options)
        option_list.action_first()
        self._update_info(self._first_id() or self._configured)
        self._shown = True

    def _first_id(self) -> str | None:
        option_list = self.query_one(OptionList)
        if not option_list.option_count:
            return None
        option = option_list.get_option_at_index(0)
        return str(option.id) if option.id is not None else None

    def _update_info(self, name: str) -> None:
        description = _DESCRIPTIONS.get(name, "built-in Textual theme")
        if name == AUTO_THEME:
            description = f"{description} → {resolve_theme_name_safely(name)}"
        self.query_one("#theme-info", Static).update(Text(description))

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        """Preview the highlighted theme across the whole screen."""
        if event.option.id is None or not self._shown:
            return
        name = str(event.option.id)
        self._update_info(name)
        self._app().theme = resolve_theme_name_safely(name)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Enter keeps the previewed theme; the app persists it."""
        event.stop()
        if event.option.id is not None:
            self.dismiss(str(event.option.id))

    def action_cancel(self) -> None:
        """Escape puts the theme that was active on entry back."""
        if self._original:
            self._app().theme = self._original
        self.dismiss(None)
