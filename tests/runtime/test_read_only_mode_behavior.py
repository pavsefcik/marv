"""Behavior tests for --read-only mode.

Read-only mode is a real narrowing of the active tool set, not an approval
prompt: a model that asks to write must be refused because the tool is not
active, and the schemas offered to the model must not advertise it either.
"""

from __future__ import annotations

import pytest

from marv.config import Config
from marv.runtime.agent import READ_ONLY_TOOLS, Agent
from tests.test_doubles.llm_provider_fake import LLMProviderFake
from tests.test_doubles.llm_stream_builders import make_text_events


def build_agent(temp_dir, *, read_only: bool):
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
    return Agent(config.to_agent_settings(), fake), fake


def test_read_only_narrows_the_active_tool_set(temp_dir):
    agent, _ = build_agent(temp_dir, read_only=True)

    assert sorted(agent.list_active_tools()) == sorted(READ_ONLY_TOOLS)


def test_read_only_omits_mutating_tools_from_the_model_schemas(temp_dir):
    agent, _ = build_agent(temp_dir, read_only=True)

    names = {schema["function"]["name"] for schema in agent.tools.get_schemas()}

    assert names == set(READ_ONLY_TOOLS)
    assert "write" not in names
    assert "edit" not in names
    assert "bash" not in names


@pytest.mark.asyncio
async def test_read_only_refuses_a_write_even_when_the_model_asks(temp_dir):
    agent, _ = build_agent(temp_dir, read_only=True)

    result = await agent.tools.execute("write", {"path": str(temp_dir / "x.txt"), "content": "hi"})

    assert result.is_error is True
    assert "Inactive tool" in result.content


@pytest.mark.asyncio
async def test_default_mode_still_exposes_every_tool(temp_dir):
    agent, _ = build_agent(temp_dir, read_only=False)

    assert sorted(agent.list_active_tools()) == sorted(
        ["read", "write", "edit", "bash", "grep", "find", "ls"]
    )
    names = {schema["function"]["name"] for schema in agent.tools.get_schemas()}
    assert "write" in names


def test_read_only_is_named_in_the_system_prompt_tool_list(temp_dir):
    agent, _ = build_agent(temp_dir, read_only=True)

    prompt = agent.get_system_prompt()

    assert "- write" not in prompt
    assert "- edit" not in prompt
    assert "- bash" not in prompt
    assert "- read" in prompt
