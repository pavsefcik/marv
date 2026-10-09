"""Input-docked menus that replace the prompt input line.

marv has no floating windows: pickers and prompts render in the same space as
the prompt input (between its two rules, growing upward as needed) and a
choice drops straight back into input mode.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from rich.text import Text
from textual.binding import Binding
from textual.widget import Widget
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

if TYPE_CHECKING:
    from collections.abc import Sequence

    from textual.app import ComposeResult

    from marv.tui.app import AgentApp


class DockPanel[T](Widget):
    """Base class for menus that replace the prompt input.

    The app mounts one panel at a time into the input dock and hides the
    prompt while it is up. ``dismiss`` hands a result to the opener's
    callback and drops straight back into input mode.
    """

    can_focus = True

    BINDINGS = [Binding("escape", "cancel", "Close", show=False)]

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.add_class("inline-panel")
        self._dismissed = False

    def on_mount(self) -> None:
        """Move keyboard focus into the panel when it appears."""
        self.focus_target()

    def focus_target(self) -> None:
        """Focus the panel's primary control (the panel itself by default)."""
        self.focus()

    def dismiss(self, result: T | None) -> None:
        """Close the panel with a result; later calls are ignored."""
        if self._dismissed:
            return
        self._dismissed = True
        cast("AgentApp", self.app).close_panel(self, result)

    def action_cancel(self) -> None:
        """Close the panel without a result."""
        self.dismiss(None)


class MenuPanel(DockPanel[str]):
    """A highlighted choice list that replaces the prompt input line.

    Up/down move, enter (or a click) chooses, escape cancels. Dismisses with
    the chosen option id.
    """

    def __init__(
        self,
        title: str,
        options: Sequence[tuple[str, str | Text]],
        *,
        subtitle: str = "",
        hint: str = "enter to choose · esc to cancel",
        empty: str = "(nothing to choose)",
        initial_id: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._title = title
        self._subtitle = subtitle
        self._options = list(options)
        self._hint = hint
        self._empty = empty
        self._initial_id = initial_id

    def compose(self) -> ComposeResult:
        yield Static(self._title, classes="panel-title")
        if self._subtitle:
            yield Static(self._subtitle, classes="panel-subtitle")
        if self._options:
            yield OptionList(
                *(Option(_plain(label), id=option_id) for option_id, label in self._options),
                id="panel-options",
                classes="panel-menu",
            )
        else:
            yield Static(self._empty, classes="panel-empty")
        yield Static(self._hint, classes="panel-hint")

    def focus_target(self) -> None:
        """Focus the list (preselecting ``initial_id`` when given)."""
        try:
            option_list = self.query_one(OptionList)
        except Exception:  # noqa: BLE001 - empty menus keep focus on the panel
            super().focus_target()
            return
        if self._initial_id is not None:
            for index in range(option_list.option_count):
                option = option_list.get_option_at_index(index)
                if option.id == self._initial_id:
                    option_list.highlighted = index
                    break
        option_list.focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Enter or a click chooses the highlighted option."""
        event.stop()
        if event.option.id is not None:
            self.choose(str(event.option.id))

    def choose(self, option_id: str) -> None:
        """Dismiss with the chosen option id."""
        self.dismiss(option_id)


def _plain(label: str | Text) -> Text:
    """Wrap a label as markup-free text (user content must not be markup)."""
    return label if isinstance(label, Text) else Text(label)
