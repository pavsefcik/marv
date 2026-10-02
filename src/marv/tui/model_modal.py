"""Model modal - pick a model and adjust thinking settings."""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.binding import Binding
from textual.containers import Container, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, RadioButton, RadioSet, Select, Static

from marv.llm.models import get_model_info, resolve_capability_provider, supports_reasoning
from marv.runtime.settings import ThinkingLevel, get_available_thinking_levels

if TYPE_CHECKING:
    from collections.abc import Callable

    from textual.app import ComposeResult

    from marv.runtime.agent import Agent


class ModelModal(ModalScreen[None]):
    """Modal for picking a model and configuring thinking settings.

    Options are built at construction time (the caller passes the discovered
    model list) so nothing calls `set_options` after mount - that would fire a
    spurious `Select.Changed` and close the modal immediately.
    """

    BINDINGS = [
        Binding("escape", "close", "Close"),
    ]

    def __init__(
        self,
        agent: Agent,
        on_change: Callable[[str | None, ThinkingLevel | None], None],
        models: list[str] | None = None,
    ) -> None:
        super().__init__()
        self._agent = agent
        self._on_change = on_change
        self._available_models = sorted(models) if models else []
        self._pending_model: str | None = None
        self._pending_thinking: ThinkingLevel | None = None

    def _model_options(self) -> list[tuple[str, str]]:
        current = self._agent.model_name
        models = list(self._available_models)
        if current and current not in models:
            models.insert(0, current)
        return [(m, m) for m in models]

    def compose(self) -> ComposeResult:
        with Container(id="model-modal"):
            yield Static("Model Settings", id="model-title")
            with VerticalScroll(id="model-content"):
                yield Static("[bold cyan]SELECT MODEL[/]", classes="section-header")
                options = self._model_options()
                current = self._agent.model_name
                if current:
                    yield Select(
                        options,
                        value=current,
                        id="model-select",
                        allow_blank=False,
                    )
                else:
                    # No model yet: start blank so the user must pick one.
                    yield Select(
                        options,
                        prompt="choose a model…",
                        id="model-select",
                        allow_blank=True,
                    )

                yield Static("[bold cyan]THINKING LEVEL[/]", classes="section-header")
                yield self._build_thinking_radio()

                yield Static("[bold cyan]SUMMARY[/]", classes="section-header")
                yield Static(self._build_summary(), id="summary-info")

            # Kept outside the scroll area so it is always reachable.
            yield Button("Save", id="save-btn", variant="primary")
            yield Static(
                "Picking a model applies it · [bold]esc[/] cancel",
                id="model-hint",
            )

    async def on_mount(self) -> None:
        """Focus the picker."""
        self.query_one("#model-select", Select).focus()

    def _build_thinking_radio(self) -> RadioSet:
        """Build thinking level radio set."""
        provider_hint = resolve_capability_provider(self._agent.provider_name)
        available = get_available_thinking_levels(self._agent.model_name, provider=provider_hint)

        buttons = []
        for level in ThinkingLevel:
            is_available = level in available
            label = level.value
            if not is_available:
                label += " (unavailable)"
            buttons.append(
                RadioButton(
                    label,
                    disabled=not is_available,
                    name=level.value,
                )
            )
        return RadioSet(*buttons, id="thinking-radio")

    def _build_summary(self) -> str:
        """Build summary of current and pending settings."""
        new_model = self._pending_model or self._agent.model_name
        new_thinking = self._pending_thinking or self._agent.thinking_level

        model_info = get_model_info(new_model)
        if model_info:
            reasoning_support = "Yes" if model_info.reasoning else "No"
            max_tokens = f"{model_info.max_output_tokens:,}"
        else:
            provider_hint = resolve_capability_provider(self._agent.provider_name)
            reasoning = supports_reasoning(new_model, provider_hint)
            reasoning_support = "Yes" if reasoning else "No"
            max_tokens = "(unknown)"

        lines = [
            f"  Provider: {self._agent.provider_name}",
            f"  Model: {new_model}",
            f"  Thinking: {new_thinking.value}",
            f"  Supports Reasoning: {reasoning_support}",
            f"  Max Output Tokens: {max_tokens}",
        ]
        return "\n".join(lines)

    def _update_summary(self) -> None:
        """Update the summary display."""
        self.query_one("#summary-info", Static).update(self._build_summary())

    async def _rebuild_thinking_radio(self, model: str) -> None:
        """Rebuild thinking radio for a new model."""
        provider_hint = resolve_capability_provider(self._agent.provider_name)
        available = get_available_thinking_levels(model, provider=provider_hint)

        buttons = []
        for level in ThinkingLevel:
            is_available = level in available
            label = level.value
            if not is_available:
                label += " (unavailable)"
            buttons.append(
                RadioButton(
                    label,
                    disabled=not is_available,
                    name=level.value,
                )
            )

        old_radio = self.query_one("#thinking-radio", RadioSet)
        new_radio = RadioSet(*buttons, id="thinking-radio")

        await old_radio.remove()
        headers = list(self.query(".section-header"))
        if len(headers) >= 2:
            await headers[1].mount(new_radio, after=headers[1])

    def _close(self) -> None:
        """Pop this modal, guarding against a duplicate pop."""
        if self.app.screen is self:
            self.app.pop_screen()

    def action_close(self) -> None:
        """Close the modal without applying."""
        self._close()

    def action_save(self) -> None:
        """Apply any pending thinking change and close."""
        if self._pending_thinking:
            self._on_change(None, self._pending_thinking)
        self._close()

    async def on_select_changed(self, event: Select.Changed) -> None:
        """Apply the picked model and close the picker immediately.

        The blank/current value present at mount is ignored, so only a real
        user choice switches the model and closes the modal.
        """
        if event.select.id != "model-select" or not event.value:
            return
        new_model = str(event.value)
        if new_model == self._agent.model_name:
            self._pending_model = None
            await self._rebuild_thinking_radio(new_model)
            self._update_summary()
            return
        self._on_change(new_model, self._pending_thinking)
        self._close()

    def on_radio_set_changed(self, event: RadioSet.Changed) -> None:
        """Handle thinking level change."""
        if event.radio_set.id == "thinking-radio" and event.pressed:
            level_name = event.pressed.name
            if level_name:
                self._pending_thinking = ThinkingLevel(level_name)
                self._update_summary()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses."""
        if event.button.id == "save-btn":
            self.action_save()
