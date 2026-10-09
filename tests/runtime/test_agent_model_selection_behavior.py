"""Behavior tests for model selection in the agent."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from marv.config import Config
from marv.runtime.agent import Agent
from marv.runtime.events import ModelSelectEvent
from marv.runtime.hooks import NullHooks
from marv.runtime.session import Session
from marv.runtime.settings import ThinkingLevel
from tests.test_doubles.llm_provider_fake import LLMProviderFake


def build_agent(
    temp_dir,
    *,
    provider_name: str = "fake",
    provider_model: str = "fake-model",
    thinking_level: ThinkingLevel = ThinkingLevel.OFF,
    session: Session | None = None,
) -> tuple[Agent, LLMProviderFake]:
    config = Config(
        provider="openai",
        model=provider_model,
        api_key="test",
        session_dir=temp_dir,
        context_max_tokens=2048,
        max_output_tokens=2048,
        thinking_level=thinking_level,
    )
    provider = LLMProviderFake([], name=provider_name, model=provider_model)
    return Agent(config.to_agent_settings(), provider, session=session), provider


@dataclass(slots=True)
class EventRecorderHooks(NullHooks):
    events: list[object]

    async def on_event(self, event, *, session=None) -> None:
        self.events.append(event)


def test_agent_set_model_updates_provider_and_persists_selection(temp_dir):
    agent, provider = build_agent(temp_dir, provider_name="openai", provider_model="gpt-5.4")

    agent.set_model("gpt-5-mini")

    assert provider.model == "gpt-5-mini"
    assert agent.session.get_model_selection() == ("openai", "gpt-5-mini")
    assert any(entry.type == "model_change" for entry in agent.session.entries)


def test_agent_set_model_noop_for_same_model_does_not_append_entry(temp_dir):
    agent, _ = build_agent(temp_dir, provider_name="openai", provider_model="gpt-5.4")
    entry_count = len(agent.session.entries)

    agent.set_model("gpt-5.4")

    assert len(agent.session.entries) == entry_count


def test_agent_set_model_clamps_thinking_level_for_lower_capability_model(temp_dir):
    agent, provider = build_agent(
        temp_dir,
        provider_name="openai",
        provider_model="gpt-5",
        thinking_level=ThinkingLevel.HIGH,
    )

    agent.set_model("gpt-4o")

    assert provider.model == "gpt-4o"
    assert agent.config.thinking_level == ThinkingLevel.OFF


def test_agent_set_model_rejects_invalid_provider_model_pair(temp_dir):
    agent, provider = build_agent(
        temp_dir, provider_name="openai-codex", provider_model="gpt-5-codex"
    )

    with pytest.raises(ValueError):
        agent.set_model("gpt-4o")

    assert provider.model == "gpt-5-codex"


def test_agent_restores_model_selection_from_session_entries(temp_dir):
    session = Session.new(temp_dir, provider="openai", model="gpt-5.4")
    session.append_model_change("openai", "gpt-5-mini")

    agent, provider = build_agent(
        temp_dir,
        provider_name="openai",
        provider_model="gpt-5.4",
        session=session,
    )

    assert provider.model == "gpt-5-mini"
    assert agent.session.get_model_selection() == ("openai", "gpt-5-mini")


def test_agent_restore_ignores_selection_for_different_provider(temp_dir):
    session = Session.new(temp_dir, provider="openai", model="gpt-5.4")
    session.append_model_change("apple-fm", "system")

    agent, provider = build_agent(
        temp_dir,
        provider_name="openai",
        provider_model="gpt-5.4",
        session=session,
    )

    assert provider.model == "gpt-5.4"
    assert agent.session.get_model_selection() == ("apple-fm", "system")


def test_agent_restore_ignores_invalid_model_for_provider(temp_dir):
    session = Session.new(temp_dir, provider="openai-codex", model="gpt-5-codex")
    session.append_model_change("openai-codex", "gpt-4o")

    agent, provider = build_agent(
        temp_dir,
        provider_name="openai-codex",
        provider_model="gpt-5-codex",
        session=session,
    )

    assert provider.model == "gpt-5-codex"


@pytest.mark.asyncio
async def test_agent_set_model_emits_model_select_event_to_hooks(temp_dir):
    agent, _ = build_agent(temp_dir, provider_name="openai", provider_model="gpt-5.4")
    recorder = EventRecorderHooks(events=[])
    agent.set_hooks(recorder)
    agent.set_model("gpt-5-mini")

    for _ in range(3):
        await asyncio.sleep(0)

    events = [event for event in recorder.events if isinstance(event, ModelSelectEvent)]
    assert len(events) == 1
    assert events[0].provider == "openai"
    assert events[0].previous_model == "gpt-5.4"
    assert events[0].model == "gpt-5-mini"
    assert events[0].source == "set"


@pytest.mark.asyncio
async def test_agent_load_session_emits_restore_model_select_event(temp_dir):
    saved = Session.new(temp_dir, provider="openai", model="gpt-5.4")
    saved.append_model_change("openai", "gpt-5-mini")

    agent, _ = build_agent(temp_dir, provider_name="openai", provider_model="gpt-5.4")
    recorder = EventRecorderHooks(events=[])
    agent.set_hooks(recorder)
    await agent.load_session(saved)

    for _ in range(3):
        await asyncio.sleep(0)

    events = [event for event in recorder.events if isinstance(event, ModelSelectEvent)]
    assert events
    restore_event = events[-1]
    assert restore_event.provider == "openai"
    assert restore_event.model == "gpt-5-mini"
    assert restore_event.source == "restore"
