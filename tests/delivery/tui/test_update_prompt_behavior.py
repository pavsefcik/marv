"""Behavior tests for the TUI's startup update offer."""

from __future__ import annotations

import asyncio

import pytest

from marv.config import Config
from marv.tui.app import AgentApp
from marv.tui.extension_ui import ConfirmPanel
from marv.updates import InstallOutcome, Release
from tests.test_doubles.llm_provider_fake import LLMProviderFake


def _config(temp_dir, **overrides) -> Config:
    return Config(
        provider="openai",
        model="gpt-4o",
        api_key="test",
        session_dir=temp_dir,
        **overrides,
    )


@pytest.mark.asyncio
async def test_tui_offers_to_install_a_newer_release(temp_dir, monkeypatch):
    release = Release("9.9.9", "v9.9.9", "https://example.invalid/marv-9.9.9.whl")
    installed: list[Release] = []

    def fake_install(self, chosen: Release) -> InstallOutcome:
        installed.append(chosen)
        return InstallOutcome(ok=True, returncode=0, stderr="")

    monkeypatch.setattr(AgentApp, "_probe_update", lambda self: release)
    monkeypatch.setattr(AgentApp, "_install_update", fake_install)

    app = AgentApp(_config(temp_dir), provider=LLMProviderFake([]))

    async with app.run_test() as pilot:
        for _ in range(100):
            if isinstance(app.active_panel, ConfirmPanel):
                break
            await asyncio.sleep(0.02)
        assert isinstance(app.active_panel, ConfirmPanel)

        await pilot.press("y")
        for _ in range(100):
            if installed:
                break
            await asyncio.sleep(0.02)

    assert installed == [release]


@pytest.mark.asyncio
async def test_tui_skips_the_probe_when_disabled(temp_dir, monkeypatch):
    probes: list[int] = []

    def fake_probe(self) -> Release | None:
        probes.append(1)
        return None

    monkeypatch.setattr(AgentApp, "_probe_update", fake_probe)

    app = AgentApp(_config(temp_dir, update_check=False), provider=LLMProviderFake([]))

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()

    assert probes == []


@pytest.mark.asyncio
async def test_tui_warns_that_starting_would_restart_the_shared_server(temp_dir, monkeypatch):
    warning = "another marv session (pid 42) is using model old/model"
    monkeypatch.setattr(AgentApp, "_shared_server_warning", lambda self: warning)

    app = AgentApp(_config(temp_dir), provider=LLMProviderFake([]))

    messages: list[str] = []
    async with app.run_test() as pilot:
        for _ in range(100):
            messages = [
                getattr(widget.render(), "plain", str(widget.render()))
                for widget in app.query_one("#chat-view").query("Static")
                if "message-system" in widget.classes
            ]
            if any(warning in message for message in messages):
                break
            await pilot.pause()

    assert any(warning in message for message in messages)
