"""Behavior tests for tool mode vs plain chat mode.

Chat mode is a real narrowing, not a cosmetic toggle: no tool schema is offered
to the model, a tool call the model emits anyway is refused by the registry, and
the system prompt stops advertising tools. The mode is agent state, so ``/new``
keeps the current choice while a fresh launch starts back in tools mode.
"""

from __future__ import annotations

import pytest

from marv.config import Config
from marv.runtime.agent import Agent
from marv.runtime.settings import InteractionMode
from tests.test_doubles.llm_provider_fake import LLMProviderFake
from tests.test_doubles.llm_stream_builders import make_text_events


def build_agent(
    temp_dir,
    *,
    read_only: bool = False,
    tools_supported: bool = True,
) -> tuple[Agent, LLMProviderFake]:
    config = Config(
        provider="openai",
        model="fake-model",
        api_key="test",
        session_dir=temp_dir,
        context_max_tokens=2048,
        max_output_tokens=2048,
        read_only=read_only,
    )
    fake = LLMProviderFake([make_text_events("ok")])
    fake.supports_tools = tools_supported
    return Agent(config.to_agent_settings(), fake), fake


def test_default_mode_offers_tools(temp_dir):
    agent, _ = build_agent(temp_dir)

    assert agent.interaction_mode is InteractionMode.TOOLS
    assert agent.tools.get_schemas()
    assert "Available tools:" in agent.get_system_prompt()


def test_chat_mode_offers_no_schemas_and_advertises_no_tools(temp_dir):
    agent, _ = build_agent(temp_dir)

    agent.set_interaction_mode(InteractionMode.CHAT)

    assert agent.list_active_tools() == []
    assert agent.tools.get_schemas() == []
    prompt = agent.get_system_prompt()
    assert "Available tools:" not in prompt
    assert "helpful, friendly assistant" in prompt
    assert "coding assistant" not in prompt
    assert "cannot read files" not in prompt


@pytest.mark.asyncio
async def test_chat_mode_refuses_a_tool_call_the_model_emits_anyway(temp_dir):
    agent, _ = build_agent(temp_dir)

    agent.set_interaction_mode(InteractionMode.CHAT)
    result = await agent.tools.execute("read", {"path": str(temp_dir / "x.txt")})

    assert result.is_error is True
    assert "Inactive tool" in result.content


def test_returning_to_tools_restores_the_previous_active_set(temp_dir):
    agent, _ = build_agent(temp_dir, read_only=True)
    before = sorted(agent.list_active_tools())

    agent.set_interaction_mode(InteractionMode.CHAT)
    assert agent.list_active_tools() == []

    agent.set_interaction_mode(InteractionMode.TOOLS)

    assert sorted(agent.list_active_tools()) == before
    assert "Available tools:" in agent.get_system_prompt()


@pytest.mark.asyncio
async def test_a_provider_without_tool_support_never_sends_schemas(temp_dir):
    agent, fake = build_agent(temp_dir, tools_supported=False)

    assert agent.interaction_mode is InteractionMode.TOOLS
    assert "Available tools:" not in agent.get_system_prompt()

    async for _ in agent.run("hello"):
        pass

    assert not fake.stream_calls[-1]["tools"]


@pytest.mark.asyncio
async def test_chat_mode_sends_no_tools_and_survives_a_new_session(temp_dir):
    agent, fake = build_agent(temp_dir)

    agent.set_interaction_mode(InteractionMode.CHAT)
    async for _ in agent.run("hello"):
        pass

    assert not fake.stream_calls[-1]["tools"]

    await agent.new_session()

    assert agent.interaction_mode is InteractionMode.CHAT
    assert agent.tools.get_schemas() == []
    assert "Available tools:" not in agent.get_system_prompt()
