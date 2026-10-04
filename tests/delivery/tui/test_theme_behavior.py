"""Behavior tests for TUI theme resolution and startup appearance detection."""

from __future__ import annotations

import os
import time

import pytest

from marv.config import Config
from marv.tui import theme as theme_module
from marv.tui.app import AgentApp
from marv.tui.theme import resolve_theme_name
from tests.test_doubles.llm_provider_fake import LLMProviderFake


@pytest.fixture(autouse=True)
def _no_appearance_probes(monkeypatch):
    """Keep resolution deterministic: no OSC-11 query, no `defaults` shell-out."""
    theme_module.reset_appearance_cache()
    monkeypatch.setattr(theme_module, "_query_terminal_appearance", lambda: None)
    monkeypatch.setattr(theme_module, "_system_appearance", lambda: None)
    yield
    theme_module.reset_appearance_cache()


def osc11_reply(spec: str) -> bytes:
    """Build the reply a terminal sends for an OSC 11 background query."""
    return f"\x1b]11;{spec}\x07".encode()


def test_theme_auto_resolves_to_dark_ansi_theme_when_terminal_is_dark(monkeypatch):
    monkeypatch.setattr(theme_module, "_query_terminal_appearance", lambda: True)

    assert resolve_theme_name("auto") == ("ansi-dark", None)


def test_theme_auto_resolves_to_light_ansi_theme_when_terminal_is_light(monkeypatch):
    monkeypatch.setattr(theme_module, "_query_terminal_appearance", lambda: False)

    assert resolve_theme_name("auto") == ("ansi-light", None)


def test_theme_auto_falls_back_to_dark_when_appearance_is_unknown():
    name, warning = resolve_theme_name("auto")

    assert name == "ansi-dark"
    assert warning is None


def test_theme_missing_configuration_defaults_to_auto():
    assert resolve_theme_name(None) == ("ansi-dark", None)
    assert resolve_theme_name("") == ("ansi-dark", None)


def test_theme_builtin_names_are_applied_as_is():
    for name in ("nord", "tokyo-night", "dracula", "ansi-light"):
        assert resolve_theme_name(name) == (name, None)


def test_theme_minimal_is_kept_for_compatibility():
    assert resolve_theme_name("minimal") == ("minimal", None)


def test_theme_unknown_name_falls_back_to_auto_with_a_warning():
    name, warning = resolve_theme_name("nonsense")

    assert name == "ansi-dark"
    assert warning is not None
    assert "nonsense" in warning


def test_theme_appearance_is_probed_once_and_cached(monkeypatch):
    calls = 0

    def counting_probe() -> bool:
        nonlocal calls
        calls += 1
        return True

    monkeypatch.setattr(theme_module, "_query_terminal_appearance", counting_probe)

    resolve_theme_name("auto")
    resolve_theme_name("auto")
    name, warning = resolve_theme_name("nonsense")

    assert name == "ansi-dark"
    assert warning is not None
    assert calls == 1


def test_theme_detection_is_none_without_a_tty(monkeypatch):
    monkeypatch.setattr(theme_module.sys, "stdin", object())
    monkeypatch.setattr(theme_module.sys, "stdout", object())

    assert theme_module.detect_dark_appearance() is None


def test_theme_detection_returns_none_when_system_appearance_is_unreadable(monkeypatch):
    monkeypatch.setattr(theme_module, "_query_terminal_appearance", lambda: None)
    monkeypatch.setattr(theme_module, "_system_appearance", lambda: None)

    assert theme_module.detect_dark_appearance() is None


@pytest.mark.parametrize(
    ("spec", "dark"),
    [
        ("rgb:0000/0000/0000", True),
        ("rgb:ffff/ffff/ffff", False),
        ("#000000", True),
        ("#fff", False),
    ],
)
def test_theme_parses_osc11_background_replies(spec, dark):
    assert theme_module._parse_osc11_response(osc11_reply(spec)) is dark


def test_theme_ignores_an_osc11_reply_it_cannot_parse():
    assert theme_module._parse_osc11_response(osc11_reply("not-a-color")) is None
    assert theme_module._parse_osc11_response(b"") is None


def test_theme_reads_a_complete_osc11_reply_from_the_terminal():
    read_fd, write_fd = os.pipe()
    try:
        os.write(write_fd, osc11_reply("rgb:ffff/ffff/ffff"))

        buffer = theme_module._read_osc11_reply(read_fd)
    finally:
        os.close(read_fd)
        os.close(write_fd)

    assert theme_module._parse_osc11_response(buffer) is False


def test_theme_terminal_probe_gives_up_within_a_bounded_time():
    read_fd, write_fd = os.pipe()
    try:
        started = time.monotonic()
        buffer = theme_module._read_osc11_reply(read_fd)
        elapsed = time.monotonic() - started
    finally:
        os.close(read_fd)
        os.close(write_fd)

    assert buffer == b""
    assert elapsed < 0.6


def make_config(temp_dir, **overrides) -> Config:
    values = {
        "provider": "openai",
        "model": "gpt-4o",
        "api_key": "test",
        "session_dir": temp_dir / "sessions",
    }
    values.update(overrides)
    return Config(**values)


def make_app(temp_dir, **overrides) -> AgentApp:
    return AgentApp(
        make_config(temp_dir, **overrides),
        provider=LLMProviderFake([], name="openai", model="gpt-4o"),
    )


@pytest.mark.asyncio
async def test_app_applies_a_builtin_theme_from_config(temp_dir):
    app = make_app(temp_dir, theme="nord")

    async with app.run_test():
        assert app.theme == "nord"


@pytest.mark.asyncio
async def test_app_applies_the_minimal_theme_from_config(temp_dir):
    app = make_app(temp_dir, theme="minimal")

    async with app.run_test():
        assert app.theme == "minimal"


@pytest.mark.asyncio
async def test_app_resolves_auto_to_an_ansi_theme(temp_dir):
    app = make_app(temp_dir, theme="auto")

    async with app.run_test():
        assert app.theme == "ansi-dark"


@pytest.mark.asyncio
async def test_app_applies_the_light_ansi_theme_when_the_terminal_is_light(temp_dir, monkeypatch):
    monkeypatch.setattr(theme_module, "_query_terminal_appearance", lambda: False)
    app = make_app(temp_dir, theme="auto")

    async with app.run_test():
        assert app.theme == "ansi-light"


@pytest.mark.asyncio
async def test_app_warns_when_the_configured_theme_is_unknown(temp_dir):
    app = make_app(temp_dir, theme="nonsense")

    async with app.run_test() as pilot:
        await pilot.pause()

        assert app.theme == "ansi-dark"
        chat = app.query_one("#chat-view")
        warnings = [
            getattr(widget.render(), "plain", str(widget.render()))
            for widget in chat.query(".message-system")
        ]
        assert any("nonsense" in message for message in warnings)
