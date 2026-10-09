"""Behavior tests for the agent loop."""

from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel

from marv.config import Config
from marv.llm.events import (
    AssistantMetadataEvent,
    DoneEvent,
    PartialMessage,
    TextDeltaEvent,
    TextEndEvent,
    TextStartEvent,
    ThinkingDeltaEvent,
    ThinkingEndEvent,
    ThinkingStartEvent,
)
from marv.runtime.agent import Agent
from marv.tools import BaseTool
from tests.test_doubles.llm_provider_fake import LLMProviderFake
from tests.test_doubles.llm_stream_builders import (
    make_error_events,
    make_text_events,
    make_tool_call_events,
)


def build_agent(
    temp_dir,
    scripts,
    *,
    provider_name: str = "fake",
    provider_model: str = "fake-model",
):
    """Create an agent wired to a fake LLM provider."""
    config = Config(
        provider="openai",
        model=provider_model,
        api_key="test",
        session_dir=temp_dir,
        context_max_tokens=2048,
        max_output_tokens=2048,
    )
    fake = LLMProviderFake(scripts, name=provider_name, model=provider_model)
    agent = Agent(config.to_agent_settings(), fake)
    return agent, fake


@pytest.mark.asyncio
async def test_agent_streams_text_and_persists_message(temp_dir):
    agent, fake = build_agent(temp_dir, [make_text_events("Hello world")])

    chunks = []
    async for chunk in agent.run("Hi"):
        chunks.append(chunk)

    text = "".join(c.payload for c in chunks if c.type == "text_delta")
    assert "Hello world" in text
    assert len(fake.stream_calls) == 1
    assert agent.session.messages[0].role.value == "system"
    assert agent.session.messages[1].content == "Hi"
    assert agent.session.messages[2].content == "Hello world"


@pytest.mark.asyncio
async def test_agent_runs_tool_call_then_continues(temp_dir):
    test_file = temp_dir / "note.txt"
    test_file.write_text("alpha")

    scripts = [
        make_tool_call_events("call_1", "read", {"path": str(test_file)}),
        make_text_events("Done."),
    ]
    agent, _ = build_agent(temp_dir, scripts)

    chunks = []
    async for chunk in agent.run("Read the file"):
        chunks.append(chunk)

    tool_results = [c.payload for c in chunks if c.type == "tool_result"]
    assert tool_results
    assert "alpha" in tool_results[0].result

    assistant_messages = [m for m in agent.session.messages if m.role.value == "assistant"]
    assert assistant_messages
    assert assistant_messages[-1].content == "Done."


@pytest.mark.asyncio
async def test_agent_retries_retryable_tool_failures_once(temp_dir):
    class NoParams(BaseModel):
        pass

    class FlakyTool(BaseTool[NoParams]):
        name = "flaky"
        description = "Fails once, then succeeds."
        parameters = NoParams

        def __init__(self) -> None:
            self.attempts = 0

        async def execute(self, params: NoParams) -> str:
            self.attempts += 1
            if self.attempts == 1:
                raise TimeoutError("temporary timeout")
            return "recovered"

    scripts = [
        make_tool_call_events("call_1", "flaky", {}),
        make_text_events("Done."),
    ]
    agent, _ = build_agent(temp_dir, scripts)
    flaky = FlakyTool()
    agent.tools.register(flaky)

    chunks = []
    async for chunk in agent.run("Run flaky tool"):
        chunks.append(chunk)

    tool_results = [c.payload for c in chunks if c.type == "tool_result"]
    assert tool_results
    assert tool_results[0].result == "recovered"
    assert flaky.attempts == 2


@pytest.mark.asyncio
async def test_agent_respects_cancel_before_stream(temp_dir):
    agent, fake = build_agent(temp_dir, [make_text_events("Should not run")])

    cancel_event = asyncio.Event()
    cancel_event.set()

    chunks = []
    async for chunk in agent.run("Hi", cancel_event=cancel_event):
        chunks.append(chunk)

    assert chunks == []
    assert len(fake.stream_calls) == 0
    assert agent.session.messages[1].content == "Hi"


@pytest.mark.asyncio
async def test_agent_records_thinking_content(temp_dir):
    events = [
        ThinkingStartEvent(content_index=0),
        ThinkingDeltaEvent(content_index=0, delta="step1"),
        ThinkingEndEvent(content_index=0, thinking="step1"),
        DoneEvent(message=PartialMessage()),
    ]
    agent, _ = build_agent(temp_dir, [events])

    chunks = []
    async for chunk in agent.run("Think"):
        chunks.append(chunk)

    assistant_messages = [m for m in agent.session.messages if m.role.value == "assistant"]
    assert assistant_messages
    assert assistant_messages[-1].thinking is not None
    assert "step1" in assistant_messages[-1].thinking.text


@pytest.mark.asyncio
async def test_agent_merges_provider_metadata(temp_dir):
    events = [
        TextStartEvent(content_index=0),
        TextDeltaEvent(content_index=0, delta="Hello"),
        TextEndEvent(content_index=0, text="Hello"),
        AssistantMetadataEvent(metadata={"openai_responses": {"output_item_id": "msg_1"}}),
        AssistantMetadataEvent(
            metadata={"openai_responses": {"reasoning_item": '{"type":"reasoning"}'}}
        ),
        DoneEvent(message=PartialMessage()),
    ]
    agent, _ = build_agent(temp_dir, [events])

    chunks = []
    async for chunk in agent.run("Hi"):
        chunks.append(chunk)

    assistant_messages = [m for m in agent.session.messages if m.role.value == "assistant"]
    assert assistant_messages
    provider_metadata = assistant_messages[-1].provider_metadata
    assert provider_metadata
    assert provider_metadata["openai_responses"]["output_item_id"] == "msg_1"
    assert provider_metadata["openai_responses"]["reasoning_item"] == '{"type":"reasoning"}'


@pytest.mark.asyncio
async def test_agent_surfaces_stream_error_as_system_message(temp_dir):
    agent, _ = build_agent(temp_dir, [make_error_events("boom")])

    chunks = []
    async for chunk in agent.run("Hello"):
        chunks.append(chunk)

    system_messages = [
        c.payload for c in chunks if c.type == "message" and c.payload.role.value == "system"
    ]
    assert system_messages
    assert "LLM stream error" in system_messages[0].content

    persisted = [m for m in agent.session.messages if m.role.value == "system"]
    assert any("LLM stream error" in m.content for m in persisted)


@pytest.mark.asyncio
async def test_agent_records_a_latency_sample_per_streamed_turn(temp_dir):
    """TTFT must be observable from the runtime, with its prompt cost."""

    agent, _ = build_agent(temp_dir, [make_text_events("Hello")])

    async for _ in agent.run("Hi"):
        pass

    metrics = agent.latency.metrics
    assert metrics.turns == 1
    assert metrics.ttft_ms >= 0.0
    # The prompt (system prompt + user turn) is non-trivial, so prefill is
    # attributed to it rather than reading as zero.
    assert metrics.prompt_tokens_per_turn > 0


@pytest.mark.asyncio
async def test_agent_prefers_a_provider_reported_ttft(temp_dir):
    """A provider timing the request itself wins over the loop's wall clock."""

    script = [
        AssistantMetadataEvent(
            metadata={
                "latency": {
                    "ttft_ms": 12.5,
                    "prompt_tokens": 40,
                    "schema_tokens": 0,
                    "streamed": True,
                }
            }
        ),
        *make_text_events("Hello"),
    ]
    agent, _ = build_agent(temp_dir, [script])

    async for _ in agent.run("Hi"):
        pass

    assert agent.latency.metrics.ttft_ms == 12.5
    assert agent.latency.metrics.turns == 1


@pytest.mark.asyncio
async def test_agent_does_not_record_a_turn_that_never_streams(temp_dir):
    """An errored turn must not be reported as a latency sample."""

    agent, _ = build_agent(temp_dir, [make_error_events("boom")])

    async for _ in agent.run("Hi"):
        pass

    assert agent.latency.metrics.turns == 0
    assert agent.latency.last_sample is None


@pytest.mark.asyncio
async def test_agent_latency_resets_between_runs(temp_dir):
    """Each run reports its own latency profile."""

    agent, _ = build_agent(
        temp_dir,
        [make_text_events("One"), make_text_events("Two"), make_text_events("Three")],
    )

    async for _ in agent.run("first"):
        pass
    assert agent.latency.metrics.turns == 1

    async for _ in agent.run("second"):
        pass
    assert agent.latency.metrics.turns == 1


@pytest.mark.asyncio
async def test_agent_emits_a_wait_estimate_before_the_request(temp_dir):
    """The UI must be able to narrate the prefill from the first second."""
    agent, _ = build_agent(temp_dir, [make_text_events("Hello")])

    chunks = []
    async for chunk in agent.run("Hi"):
        chunks.append(chunk)

    estimates = [chunk for chunk in chunks if chunk.type == "wait_estimate"]
    assert len(estimates) == 1
    estimate = estimates[0].payload
    assert estimate.prompt_tokens > 0
    # Tool schemas are on the wire every turn and cost prefill too.
    assert estimate.schema_tokens > 0
    assert estimate.prefill_tokens == estimate.prompt_tokens + estimate.schema_tokens
    # No measured history yet, so there is honestly no ETA to show.
    assert estimate.eta_ms is None


@pytest.mark.asyncio
async def test_agent_estimate_uses_an_installed_calibration(temp_dir):
    """Once a fit exists, the pre-request estimate is derived from it."""
    from marv.llm.latency import LatencyFit, LatencySample

    agent, _ = build_agent(temp_dir, [make_text_events("Hello")])
    agent.set_latency_fit(
        LatencyFit.from_samples([LatencySample(ttft_ms=1000.0, prompt_tokens=5000, streamed=True)])
    )

    chunks = []
    async for chunk in agent.run("Hi"):
        chunks.append(chunk)

    estimate = next(c for c in chunks if c.type == "wait_estimate").payload
    assert estimate.has_eta
    # 5000 tok/s means the whole prefill (prompt + schemas) is predicted.
    assert estimate.eta_ms == estimate.prefill_tokens / 5000.0 * 1000.0


@pytest.mark.asyncio
async def test_agent_fit_absorbs_a_measured_turn(temp_dir):
    """A completed turn must improve the calibration so the next ETA is better."""
    script = [
        AssistantMetadataEvent(
            metadata={
                "latency": {
                    "ttft_ms": 2500.0,
                    "prompt_tokens": 10000,
                    "schema_tokens": 1000,
                    "streamed": True,
                }
            }
        ),
        *make_text_events("Hello"),
    ]
    agent, _ = build_agent(temp_dir, [script])
    assert agent.latency_fit.has_data is False

    async for _ in agent.run("Hi"):
        pass

    fit = agent.refine_latency_fit()
    assert fit.sample_count == 1
    # The agent's own prompt/schema estimate is what the fit folds in; the
    # point is that the measured TTFT made it into a usable rate.
    assert fit.prefill_tokens_per_second > 0


@pytest.mark.asyncio
async def test_agent_prefers_a_provider_reported_schema_cost(temp_dir):
    """The transport measures the tools payload it actually put on the wire."""
    script = [
        AssistantMetadataEvent(
            metadata={
                "latency": {
                    "ttft_ms": 10.0,
                    "prompt_tokens": 40,
                    "schema_tokens": 1234,
                    "streamed": True,
                }
            }
        ),
        *make_text_events("Hello"),
    ]
    agent, _ = build_agent(temp_dir, [script])

    async for _ in agent.run("Hi"):
        pass

    assert agent.latency.last_sample is not None
    assert agent.latency.last_sample.schema_tokens == 1234
