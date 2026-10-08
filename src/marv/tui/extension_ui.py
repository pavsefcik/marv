"""Input-docked panels for extension-owned UI interactions."""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual import on
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Button, Input, ListItem, ListView, Static

from marv.tui.panel import DockPanel, MenuPanel

if TYPE_CHECKING:
    from textual.app import ComposeResult
    from textual.timer import Timer

    from marv.extensions.api import PresentedView, ViewControl


class PromptPanel(DockPanel[str]):
    """One-line text prompt; dismisses with the entered value."""

    def __init__(self, prompt: str, default: str | None = None) -> None:
        super().__init__()
        self._prompt = prompt
        self._default = default or ""

    def compose(self) -> ComposeResult:
        yield Static(self._prompt, classes="panel-title")
        yield Input(
            value=self._default,
            placeholder="type here and press enter",
            id="extension-prompt-input",
        )
        yield Static("enter to submit · esc to cancel", classes="panel-hint")

    def focus_target(self) -> None:
        self.query_one("#extension-prompt-input", Input).focus()

    @on(Input.Submitted, "#extension-prompt-input")
    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value)


class ConfirmPanel(DockPanel[bool]):
    """Yes/no confirmation; ``y`` approves, ``n``/escape denies."""

    BINDINGS = [
        Binding("escape", "deny", "Deny", show=False),
        Binding("y", "approve", "Approve", show=False),
        Binding("n", "deny", "Deny", show=False),
        Binding("enter", "approve", "Approve", show=False),
    ]

    def __init__(self, prompt: str) -> None:
        super().__init__()
        self._prompt = prompt

    def compose(self) -> ComposeResult:
        yield Static(self._prompt, classes="panel-title")
        yield Static("y approve · n deny", classes="panel-hint")

    def action_approve(self) -> None:
        self.dismiss(True)

    def action_deny(self) -> None:
        self.dismiss(False)


class SelectPanel(MenuPanel):
    """Single-select menu; dismisses with the chosen option."""

    def __init__(self, prompt: str, options: list[str]) -> None:
        super().__init__(
            prompt,
            [(option, option) for option in options],
            hint="enter to choose · esc to cancel",
        )


class PresentedViewPanel(DockPanel[object]):
    """Host a small custom extension-provided view at the input line."""

    def __init__(self, view: PresentedView[object]) -> None:
        super().__init__()
        self._view = view
        self._controls = view.controls()
        self._timer: Timer | None = None

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="extension-presented-scroll"):
            yield Static(self._view.render(), id="extension-presented-content")
        for control in self._non_button_controls():
            if control.kind == "input":
                if control.label:
                    yield Static(control.label, classes="extension-presented-control-label")
                yield Input(
                    placeholder=control.placeholder or "Type input and press Enter",
                    id=self._control_id(control),
                )
            elif control.kind == "select":
                if control.label:
                    yield Static(control.label, classes="extension-presented-control-label")
                with VerticalScroll(classes="extension-presented-select-content"):
                    items = [ListItem(Static(option), name=option) for option in control.options]
                    yield ListView(*items, id=self._control_id(control), initial_index=0)
        buttons = self._button_controls()
        if buttons:
            with Horizontal(classes="extension-modal-actions"):
                for control in buttons:
                    yield Button(
                        control.label or control.name.title(),
                        id=self._control_id(control),
                        variant="primary" if control.primary else "default",
                    )
        yield Static("esc to close", classes="panel-hint")

    def on_mount(self) -> None:
        self._timer = self.set_interval(0.1, self._refresh)
        super().on_mount()

    def focus_target(self) -> None:
        first = self._first_focusable_control()
        if first is None:
            self.focus()
        elif first.kind == "input":
            self.query_one(f"#{self._control_id(first)}", Input).focus()
        elif first.kind == "select":
            self.query_one(f"#{self._control_id(first)}", ListView).focus()
        elif first.kind == "button":
            self.query_one(f"#{self._control_id(first)}", Button).focus()

    @on(Input.Submitted)
    def on_input_submitted(self, event: Input.Submitted) -> None:
        control = self._control_by_id(event.input.id)
        if control is None:
            return
        self._dispatch(control, event.value)
        event.input.value = ""

    @on(ListView.Selected)
    def on_list_selected(self, event: ListView.Selected) -> None:
        control = self._control_by_id(event.list_view.id)
        if control is None:
            return
        value = event.item.name if isinstance(event.item.name, str) else None
        self._dispatch(control, value)

    @on(Button.Pressed)
    def on_button_pressed(self, event: Button.Pressed) -> None:
        control = self._control_by_id(event.button.id)
        if control is None:
            return
        self._dispatch(control, None)

    def _dispatch(self, control: ViewControl, value: str | None) -> None:
        self._view.handle_action(control.name, value)
        self._finish_if_done()
        self._refresh()

    def _control_id(self, control: ViewControl) -> str:
        return f"extension-presented-{control.kind}-{control.name}"

    def _control_by_id(self, control_id: str | None) -> ViewControl | None:
        if control_id is None:
            return None
        for control in self._controls:
            if self._control_id(control) == control_id:
                return control
        return None

    def _non_button_controls(self) -> list[ViewControl]:
        return [control for control in self._controls if control.kind != "button"]

    def _button_controls(self) -> list[ViewControl]:
        return [control for control in self._controls if control.kind == "button"]

    def _first_focusable_control(self) -> ViewControl | None:
        for kind in ("input", "select", "button"):
            for control in self._controls:
                if control.kind == kind:
                    return control
        return None

    def _refresh(self) -> None:
        if self._dismissed:
            return
        self.query_one("#extension-presented-content", Static).update(self._view.render())
        self._finish_if_done()

    def _finish_if_done(self) -> None:
        if self._view.is_done():
            self.dismiss(self._view.result())

    def dismiss(self, result: object | None) -> None:
        self._stop_timer()
        super().dismiss(result)

    def _stop_timer(self) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
