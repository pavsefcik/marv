"""Model menu — switch model or thinking level from the input line."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from rich.text import Text
from textual.binding import Binding
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from marv.config.state import load_last_used
from marv.llm.models import get_model_info, resolve_capability_provider, supports_reasoning
from marv.runtime.settings import ThinkingLevel, get_available_thinking_levels
from marv.tui.panel import DockPanel

if TYPE_CHECKING:
    from textual.app import ComposeResult

    from marv.llm.latency import LatencyFit
    from marv.runtime.agent import Agent


def format_latency_tier(fit: LatencyFit) -> str:
    """One-line latency tier for a calibrated model (empty when unknown).

    ``bench`` and the agent's own turns record a prefill rate and a cold-start
    constant per model; this renders them for the model menu. An uncalibrated
    model yields an empty string so the menu shows no number rather than an
    invented one.
    """
    parts: list[str] = []
    if fit.cold_start_ms > 0:
        parts.append(f"≈{fit.cold_start_ms / 1000:.1f}s TTFT")
    if fit.has_data:
        parts.append(f"{fit.prefill_tokens_per_second:,.0f} tok/s")
    return " · ".join(parts)


@dataclass(slots=True)
class ModelChoice:
    """What a visit to the model menu decided to change (at most one)."""

    model: str | None = None
    thinking: ThinkingLevel | None = None


class ModelPanel(DockPanel[ModelChoice]):
    """Menu for switching model, with a thinking-level submenu.

    Choosing an entry applies it immediately and returns to input mode.
    Escape steps back from the thinking submenu to the model list, and from
    there back to the input.
    """

    _THINKING_ID = "__thinking__"

    BINDINGS = [
        Binding("escape", "cancel", "Back", show=False),
    ]

    def __init__(self, agent: Agent, models: list[str] | None = None) -> None:
        super().__init__()
        self._agent = agent
        self._available_models = sorted(models) if models else []
        self._stage = "root"

    # ---- layout ---------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Static("", id="model-title", classes="panel-title")
        yield Static("", id="model-subtitle", classes="panel-subtitle")
        yield OptionList(id="panel-options", classes="panel-menu")
        yield Static("", id="model-info", classes="panel-subtitle")
        yield Static("", id="model-hint", classes="panel-hint")

    def on_mount(self) -> None:
        self._show_root()
        self.focus_target()

    def focus_target(self) -> None:
        """Focus the choice list."""
        self.query_one(OptionList).focus()

    # ---- stages ---------------------------------------------------------

    def _show_root(self) -> None:
        self._stage = "root"
        self.query_one("#model-title", Static).update(Text("model & thinking"))
        current = self._agent.model_name
        subtitle = f"current: {current or '(none)'} · thinking: {self._agent.thinking_level.value}"
        self.query_one("#model-subtitle", Static).update(Text(subtitle))
        self.query_one("#model-hint", Static).update(Text("enter to switch · esc to close"))

        options: list[Option] = []
        for model in self._model_list():
            label = f"{model}  // current" if model == current else model
            options.append(Option(Text(label), id=model))
        if current:
            options.append(
                Option(
                    Text(f"thinking: {self._agent.thinking_level.value}  ›"),
                    id=self._THINKING_ID,
                )
            )
        self._set_options(options)
        if not options:
            self.query_one("#model-info", Static).update(Text("no models available"))
        else:
            self._update_info_for(str(self._first_option_id() or ""))

    def _show_thinking(self) -> None:
        self._stage = "thinking"
        model = self._agent.model_name or "(none)"
        self.query_one("#model-title", Static).update(Text("thinking level"))
        self.query_one("#model-subtitle", Static).update(Text(f"model: {model}"))
        self.query_one("#model-hint", Static).update(Text("enter to set · esc back"))

        provider_hint = resolve_capability_provider(self._agent.provider_name)
        available = get_available_thinking_levels(self._agent.model_name, provider=provider_hint)
        options = [
            Option(
                Text(level.value if level in available else f"{level.value}  (unavailable)"),
                id=level.value,
                disabled=level not in available,
            )
            for level in ThinkingLevel
        ]
        self._set_options(options)
        self.query_one("#model-info", Static).update(
            Text(f"available: {', '.join(level.value for level in available)}")
        )

    def _set_options(self, options: list[Option]) -> None:
        option_list = self.query_one(OptionList)
        option_list.clear_options()
        if options:
            option_list.add_options(options)
            option_list.action_first()
        else:
            self.query_one("#model-info", Static).update(Text(""))

    def _model_list(self) -> list[str]:
        current = self._agent.model_name
        models = list(self._available_models)
        if current and current not in models:
            models.insert(0, current)
        return models

    def _first_option_id(self) -> str | None:
        option_list = self.query_one(OptionList)
        if not option_list.option_count:
            return None
        option = option_list.get_option_at_index(0)
        return str(option.id) if option.id is not None else None

    # ---- choosing -------------------------------------------------------

    def action_cancel(self) -> None:
        """Escape steps back from the submenu; from the root it closes."""
        if self._stage == "thinking":
            self._show_root()
        else:
            self.dismiss(None)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Enter or a click applies the highlighted entry."""
        event.stop()
        option_id = event.option.id
        if option_id is None:
            return
        if self._stage == "root":
            if str(option_id) == self._THINKING_ID:
                self._show_thinking()
                return
            self.dismiss(ModelChoice(model=str(option_id)))
            return
        self.dismiss(ModelChoice(thinking=ThinkingLevel(str(option_id))))

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        """Describe the highlighted entry in the info line."""
        if event.option.id is not None:
            self._update_info_for(str(event.option.id))

    def _update_info_for(self, option_id: str) -> None:
        info = self.query_one("#model-info", Static)
        if self._stage == "thinking" or option_id == self._THINKING_ID:
            provider_hint = resolve_capability_provider(self._agent.provider_name)
            available = get_available_thinking_levels(
                self._agent.model_name, provider=provider_hint
            )
            info.update(Text(f"available: {', '.join(level.value for level in available)}"))
            return
        model_info = get_model_info(option_id)
        if model_info:
            reasoning = "yes" if model_info.reasoning else "no"
            max_tokens = f"{model_info.max_output_tokens:,}"
        else:
            provider_hint = resolve_capability_provider(self._agent.provider_name)
            reasoning = "yes" if supports_reasoning(option_id, provider_hint) else "no"
            max_tokens = "(unknown)"
        parts = [f"reasoning: {reasoning}", f"max output: {max_tokens}"]
        tier = self._latency_tier(option_id)
        if tier:
            parts.append(tier)
        info.update(Text(" · ".join(parts)))

    def _latency_tier(self, model: str) -> str:
        """The calibrated latency tier for ``model``, read from state.toml."""
        fit = load_last_used(self._agent.config.session_dir).fit_for(model)
        return format_latency_tier(fit)
