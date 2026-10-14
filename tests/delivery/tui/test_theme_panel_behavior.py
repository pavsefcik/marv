"""Behavior tests for the in-TUI theme chooser.

Choosing a theme is a visual decision, so the menu previews the highlighted
entry on the whole screen, restores it on Escape, and persists only on Enter.
"""

from __future__ import annotations

import pytest
from textual.widgets import OptionList, Static, TextArea

from marv.config import Config
from marv.config.state import load_last_used
from marv.runtime.session import Session
from marv.tui import theme as theme_module
from marv.tui.app import AgentApp
from marv.tui.chat import ChatView
from marv.tui.theme import available_theme_names
from marv.tui.theme_panel import ThemePanel
from tests.test_doubles.llm_provider_fake import LLMProviderFake


@pytest.fixture(autouse=True)
def _no_appearance_probes(monkeypatch):
    """Deterministic appearance: no OSC 11 query, no `defaults` shell-out."""
    theme_module.reset_appearance_cache()
    monkeypatch.setattr(theme_module, "_query_terminal_appearance", lambda: None)
    monkeypatch.setattr(theme_module, "_system_appearance", lambda: None)
    yield
    theme_module.reset_appearance_cache()


def make_app(temp_dir, **overrides) -> AgentApp:
    values = {
        "provider": "openai",
        "model": "gpt-4o",
        "api_key": "test",
        "theme": "marv-mlx",
        "session_dir": temp_dir,
    }
    values.update(overrides)
    return AgentApp(
        Config(**values),
        provider=LLMProviderFake([], name="openai", model="gpt-4o"),
        session=Session.new(temp_dir),
    )


def option_ids(panel: ThemePanel) -> list[str]:
    options = panel.query_one("#panel-options", OptionList)
    return [str(options.get_option_at_index(i).id) for i in range(options.option_count)]


def highlight(panel: ThemePanel, name: str) -> OptionList:
    options = panel.query_one("#panel-options", OptionList)
    options.highlighted = option_ids(panel).index(name)
    return options


def system_messages(app: AgentApp) -> str:
    chat = app.query_one("#chat-view", ChatView)
    return "\n".join(getattr(widget.render(), "plain", "") for widget in chat.query(Static))


@pytest.mark.asyncio
async def test_theme_command_opens_the_chooser_marking_the_current_theme(temp_dir):
    app = make_app(temp_dir)

    async with app.run_test() as pilot:
        await pilot.pause()

        input_widget = app.query_one("#prompt-inner", TextArea)
        input_widget.focus()
        input_widget.text = "/theme "
        await pilot.press("enter")
        await pilot.pause()

        panel = app.active_panel
        assert isinstance(panel, ThemePanel)
        options = panel.query_one("#panel-options", OptionList)
        assert options.get_option_at_index(options.highlighted).id == "marv-mlx"
        assert "auto" in option_ids(panel)
        assert "nord" in option_ids(panel)


@pytest.mark.asyncio
async def test_highlighting_a_theme_previews_it_and_escape_restores(temp_dir):
    app = make_app(temp_dir)

    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.theme == "marv-mlx"

        app.open_theme_panel()
        await pilot.pause()
        panel = app.active_panel
        assert isinstance(panel, ThemePanel)

        highlight(panel, "nord")
        await pilot.pause()
        assert app.theme == "nord"

        await pilot.press("escape")
        await pilot.pause()

        assert app.active_panel is None
        assert app.theme == "marv-mlx"
        assert load_last_used(temp_dir).theme is None


@pytest.mark.asyncio
async def test_choosing_a_theme_applies_and_persists_it(temp_dir):
    app = make_app(temp_dir)

    async with app.run_test() as pilot:
        await pilot.pause()

        app.open_theme_panel()
        await pilot.pause()
        panel = app.active_panel
        assert isinstance(panel, ThemePanel)

        options = highlight(panel, "nord")
        await pilot.pause()
        options.action_select()
        await pilot.pause()

        assert app.active_panel is None
        assert app.theme == "nord"

    assert load_last_used(temp_dir).theme == "nord"


@pytest.mark.asyncio
async def test_theme_auto_uses_the_system_appearance_without_probing(temp_dir, monkeypatch):
    """A mid-session ``auto`` must not send an OSC 11 query Textual would read."""
    monkeypatch.setattr(theme_module, "_system_appearance", lambda: False)  # light

    def _forbidden() -> bool | None:
        raise AssertionError("mid-session theme change probed the terminal")

    monkeypatch.setattr(theme_module, "_query_terminal_appearance", _forbidden)
    app = make_app(temp_dir)

    async with app.run_test() as pilot:
        await pilot.pause()

        input_widget = app.query_one("#prompt-inner", TextArea)
        input_widget.focus()
        input_widget.text = "/theme auto"
        await pilot.press("enter")
        await pilot.pause()

        assert app.theme == "ansi-light"
        assert load_last_used(temp_dir).theme == "auto"


@pytest.mark.asyncio
async def test_unknown_theme_name_is_rejected_without_changing_the_theme(temp_dir):
    app = make_app(temp_dir)

    async with app.run_test() as pilot:
        await pilot.pause()

        input_widget = app.query_one("#prompt-inner", TextArea)
        input_widget.focus()
        input_widget.text = "/theme nonsense"
        await pilot.press("enter")
        await pilot.pause()

        assert app.active_panel is None
        assert app.theme == "marv-mlx"
        assert "unknown theme: nonsense" in system_messages(app)


def test_available_theme_names_puts_the_marv_presets_first():
    names = available_theme_names()

    assert names[:3] == ["auto", "marv-mlx", "minimal"]
    assert "nord" in names
    assert len(names) == len(set(names))
