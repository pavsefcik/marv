"""OpenAI-compatible provider tests using MockTransport (behavioral)."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from marv.llm.events import StreamOptions, TextBlock, ToolCallBlock
from marv.llm.openai_compat import (
    DEFAULT_READ_TIMEOUT,
    OpenAICompatibleProvider,
    PrefillTimeoutError,
    resolve_read_timeout,
)
from marv.llm.retry import RetryConfig, _is_retriable
from marv.runtime.message import Message


def _sse_payload(events: list[dict[str, object]]) -> bytes:
    chunks = []
    for event in events:
        chunks.append(f"data: {json.dumps(event)}\n\n".encode())
    chunks.append(b"data: [DONE]\n\n")
    return b"".join(chunks)


class _ReadThenFail(httpx.AsyncByteStream):
    """Emit some SSE lines, then raise a read timeout mid-stream."""

    def __init__(self, lines: list[bytes]) -> None:
        self._lines = lines

    async def __aiter__(self):
        for line in self._lines:
            yield line
        raise httpx.ReadTimeout("read timed out")


@pytest.mark.asyncio
async def test_openai_compatible_payload_and_streams_text_and_tool_call() -> None:
    captured = SimpleNamespace(json=None)
    prompt_tokens = 1
    completion_tokens = 2
    tool_id = "tool_call_123456"
    tool_id_max_length = 9
    truncated_id = tool_id[:tool_id_max_length]
    tool_args = '{"path":"/tmp/x"}'

    events = [
        {"choices": [{"delta": {"content": "Hi"}}]},
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": tool_id,
                                "function": {"name": "read", "arguments": tool_args},
                            }
                        ]
                    }
                }
            ]
        },
        {
            "choices": [{"delta": {}, "finish_reason": "stop"}],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
            },
        },
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        captured.json = json.loads(request.content.decode("utf-8"))
        assert request.url.path == "/v1/chat/completions"
        return httpx.Response(200, content=_sse_payload(events))

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(base_url="https://mistral.ai", transport=transport)
    provider = OpenAICompatibleProvider(
        base_url="https://mistral.ai",
        api_key="sk-test",
        model="mistral-model",
        http_client=client,
    )

    tools = [
        {
            "type": "function",
            "function": {
                "name": "read",
                "description": "Read a file",
                "parameters": {"type": "object"},
            },
        }
    ]

    stream = provider.stream([Message.user("hi")], tools=tools)
    result = await stream.result()

    await provider.client.aclose()

    assert captured.json is not None
    assert captured.json.get("model") == provider.model
    assert "max_tokens" in captured.json
    assert "max_completion_tokens" not in captured.json
    assert captured.json.get("tool_choice") == "auto"

    text = next(block for block in result.content if isinstance(block, TextBlock))
    tool = next(block for block in result.content if isinstance(block, ToolCallBlock))

    assert text.text == "Hi"
    assert tool.id == truncated_id
    assert tool.arguments == {"path": "/tmp/x"}
    assert result.usage.input == prompt_tokens
    assert result.usage.output == completion_tokens


@pytest.mark.asyncio
async def test_openai_compatible_streams_error_on_non_2xx_response() -> None:
    error_message = "bad request"
    http_bad_request = 400

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(http_bad_request, json={"error": {"message": error_message}})

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(base_url="https://example.com", transport=transport)
    provider = OpenAICompatibleProvider(
        base_url="https://example.com",
        api_key="sk-test",
        model="gpt-4o",
        http_client=client,
    )

    stream = provider.stream([Message.user("hi")])
    result = await stream.result()

    await provider.client.aclose()

    assert result.stop_reason == "error"
    assert result.error_message is not None
    assert error_message in result.error_message


@pytest.mark.asyncio
async def test_openai_compatible_retries_on_transient_error() -> None:
    call_count = {"count": 0}
    prompt_tokens = 1
    completion_tokens = 1
    http_server_error = 500

    events = [
        {"choices": [{"delta": {"content": "Hi"}}]},
        {
            "choices": [{"delta": {}, "finish_reason": "stop"}],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
            },
        },
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["count"] += 1
        if call_count["count"] == 1:
            return httpx.Response(http_server_error, json={"error": {"message": "transient"}})
        return httpx.Response(200, content=_sse_payload(events))

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(base_url="https://example.com", transport=transport)
    provider = OpenAICompatibleProvider(
        base_url="https://example.com",
        api_key="sk-test",
        model="gpt-4o",
        http_client=client,
        retry_config=RetryConfig(max_retries=1, initial_delay=0.0, max_delay=0.0, jitter=False),
    )

    stream = provider.stream([Message.user("hi")])
    result = await stream.result()

    await provider.client.aclose()

    assert call_count["count"] == 2
    text = next(block for block in result.content if isinstance(block, TextBlock))
    assert text.text == "Hi"


@pytest.mark.asyncio
async def test_openai_compatible_surfaces_in_stream_error_payload() -> None:
    """A top-level `error` in the SSE body is not a successful empty stream.

    The mlx_vlm server reports Metal OOM this way after an HTTP 200 was sent.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        body = b'data: {"error": "[METAL] Insufficient Memory"}\n\n'
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body)

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(base_url="http://127.0.0.1:11500", transport=transport)
    provider = OpenAICompatibleProvider(
        base_url="http://127.0.0.1:11500",
        api_key="",
        model="prism-ml/Ternary-Bonsai-2-27B-mlx-2bit",
        http_client=client,
    )

    stream = provider.stream([Message.user("hi")])
    result = await stream.result()

    await provider.client.aclose()

    assert result.stop_reason == "error"
    assert result.error_message is not None
    assert "Insufficient Memory" in result.error_message
    assert result.content == []


@pytest.mark.asyncio
async def test_openai_compatible_surfaces_truncated_stream() -> None:
    """A stream with no finish reason and no [DONE] is an error, not a stop."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = b'data: {"choices": [{"delta": {"content": "Hi"}}]}\n\n'
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body)

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(base_url="http://127.0.0.1:11500", transport=transport)
    provider = OpenAICompatibleProvider(
        base_url="http://127.0.0.1:11500",
        api_key="",
        model="prism-ml/Ternary-Bonsai-2-27B-mlx-2bit",
        http_client=client,
    )

    stream = provider.stream([Message.user("hi")])
    result = await stream.result()

    await provider.client.aclose()

    assert result.stop_reason == "error"
    assert result.error_message is not None
    assert "truncated" in result.error_message or "before completion" in result.error_message


@pytest.mark.asyncio
async def test_openai_compatible_cancels_before_request() -> None:
    cancel_event = asyncio.Event()
    cancel_event.set()

    provider = OpenAICompatibleProvider(
        base_url="https://example.com",
        api_key="sk-test",
        model="gpt-4o",
    )

    options = StreamOptions(cancel_event=cancel_event)

    stream = provider.stream([Message.user("hi")], options=options)
    result = await stream.result()

    assert result.stop_reason == "aborted"


@pytest.mark.asyncio
async def test_openai_compatible_list_models_fetches_models_endpoint() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/models"
        return httpx.Response(
            200,
            json={"data": [{"id": "model-b"}, {"id": "model-a"}]},
        )

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(base_url="https://example.com", transport=transport)
    provider = OpenAICompatibleProvider(
        base_url="https://example.com",
        api_key="sk-test",
        model="gpt-4o",
        http_client=client,
    )

    models = await provider.list_models()
    await provider.client.aclose()

    assert models == ["model-a", "model-b"]


def test_openai_compatible_set_model_allows_cross_family_model_ids() -> None:
    provider = OpenAICompatibleProvider(
        base_url="https://example.com",
        api_key="sk-test",
        model="gpt-4o",
    )

    provider.set_model("claude-sonnet-4-5")

    assert provider.model == "claude-sonnet-4-5"


@pytest.mark.asyncio
async def test_openai_compatible_reports_ttft_and_prompt_cost_as_metadata() -> None:
    """TTFT must be observable so a latency regression is attributable.

    The provider emits it on the existing assistant_metadata channel together
    with the prompt/schema token estimate that caused it.
    """
    events = [
        {"choices": [{"delta": {"content": "Hi"}}]},
        {"choices": [{"delta": {}, "finish_reason": "stop"}]},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_sse_payload(events))

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(base_url="http://127.0.0.1:11500", transport=transport)
    provider = OpenAICompatibleProvider(
        base_url="http://127.0.0.1:11500",
        api_key="",
        model="some/local-model",
        http_client=client,
    )

    tools = [
        {
            "type": "function",
            "function": {
                "name": "read",
                "description": "Read a file",
                "parameters": {"type": "object"},
            },
        }
    ]

    stream = provider.stream([Message.user("hello world")], tools=tools)

    latency: dict[str, object] | None = None
    async for event in stream:
        if event.type == "assistant_metadata" and "latency" in event.metadata:
            latency = event.metadata["latency"]

    await provider.client.aclose()

    assert latency is not None
    assert latency["streamed"] is True
    assert latency["ttft_ms"] >= 0.0
    assert isinstance(latency["prompt_tokens"], int)
    assert latency["prompt_tokens"] > 0
    # Schemas are prefill cost too, and must be attributed separately.
    assert isinstance(latency["schema_tokens"], int)
    assert latency["schema_tokens"] > 0


@pytest.mark.asyncio
async def test_openai_compatible_reports_no_ttft_for_a_turn_that_never_streams() -> None:
    """A truncated turn must report streamed=False, not a bogus TTFT."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = b'data: {"choices": [{"delta": {}, "finish_reason": "stop"}]}\n\n'
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body)

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(base_url="http://127.0.0.1:11500", transport=transport)
    provider = OpenAICompatibleProvider(
        base_url="http://127.0.0.1:11500",
        api_key="",
        model="some/local-model",
        http_client=client,
    )

    stream = provider.stream([Message.user("hi")])

    latency: dict[str, object] | None = None
    async for event in stream:
        if event.type == "assistant_metadata" and "latency" in event.metadata:
            latency = event.metadata["latency"]

    await provider.client.aclose()

    assert latency is not None
    assert latency["streamed"] is False
    assert latency["ttft_ms"] == 0.0


@pytest.mark.asyncio
async def test_openai_compatible_times_the_successful_attempt_after_a_retry() -> None:
    """A retried turn must still report a real TTFT for the attempt that won.

    The first attempt fails; the retry's first token must be measured against
    the retry's send time, not the original one (which would inflate TTFT), and
    must not be reported as never-streamed.
    """
    call_count = {"count": 0}
    events = [
        {"choices": [{"delta": {"content": "Hi"}}]},
        {"choices": [{"delta": {}, "finish_reason": "stop"}]},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["count"] += 1
        if call_count["count"] == 1:
            return httpx.Response(500, json={"error": {"message": "transient"}})
        return httpx.Response(200, content=_sse_payload(events))

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(base_url="http://127.0.0.1:11500", transport=transport)
    provider = OpenAICompatibleProvider(
        base_url="http://127.0.0.1:11500",
        api_key="",
        model="some/local-model",
        http_client=client,
        retry_config=RetryConfig(max_retries=1, initial_delay=0.0, max_delay=0.0, jitter=False),
    )

    stream = provider.stream([Message.user("hi")])

    latency: dict[str, object] | None = None
    async for event in stream:
        if event.type == "assistant_metadata" and "latency" in event.metadata:
            latency = event.metadata["latency"]

    await provider.client.aclose()

    assert call_count["count"] == 2
    assert latency is not None
    assert latency["streamed"] is True
    assert latency["ttft_ms"] >= 0.0


@pytest.mark.asyncio
async def test_openai_compatible_does_not_retry_a_read_timeout_before_the_first_token() -> None:
    """A prefill timeout must not be retried.

    On a local model, silence before the first token means the prompt is still
    being prefilled. Retrying throws away minutes of GPU work and restarts the
    whole prefill, so it is surfaced once instead of re-run.
    """
    call_count = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["count"] += 1
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=_ReadThenFail([]),
        )

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(base_url="http://127.0.0.1:11500", transport=transport)
    provider = OpenAICompatibleProvider(
        base_url="http://127.0.0.1:11500",
        api_key="",
        model="some/local-model",
        http_client=client,
        retry_config=RetryConfig(max_retries=3, initial_delay=0.0, max_delay=0.0, jitter=False),
    )

    stream = provider.stream([Message.user("hi")])
    result = await stream.result()

    await provider.client.aclose()

    assert call_count["count"] == 1
    assert result.stop_reason == "error"
    assert result.error_message is not None
    assert "AGENT_LLM_READ_TIMEOUT" in result.error_message
    assert result.content == []


@pytest.mark.asyncio
async def test_openai_compatible_retries_a_read_timeout_after_the_first_token() -> None:
    """A transport drop after tokens have streamed is still retriable."""
    call_count = {"count": 0}
    events = [
        {"choices": [{"delta": {"content": "Hi"}}]},
        {"choices": [{"delta": {}, "finish_reason": "stop"}]},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["count"] += 1
        if call_count["count"] == 1:
            body = b'data: {"choices": [{"delta": {"content": "Hi"}}]}\n\n'
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                stream=_ReadThenFail([body]),
            )
        return httpx.Response(200, content=_sse_payload(events))

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(base_url="http://127.0.0.1:11500", transport=transport)
    provider = OpenAICompatibleProvider(
        base_url="http://127.0.0.1:11500",
        api_key="",
        model="some/local-model",
        http_client=client,
        retry_config=RetryConfig(max_retries=2, initial_delay=0.0, max_delay=0.0, jitter=False),
    )

    stream = provider.stream([Message.user("hi")])
    result = await stream.result()

    await provider.client.aclose()

    assert call_count["count"] == 2
    assert result.stop_reason == "stop"
    assert result.error_message is None


def test_resolve_read_timeout_defaults_and_overrides(monkeypatch) -> None:
    monkeypatch.delenv("AGENT_LLM_READ_TIMEOUT", raising=False)
    assert resolve_read_timeout(DEFAULT_READ_TIMEOUT) == DEFAULT_READ_TIMEOUT
    assert resolve_read_timeout(None) is None

    monkeypatch.setenv("AGENT_LLM_READ_TIMEOUT", "300")
    assert resolve_read_timeout(DEFAULT_READ_TIMEOUT) == 300.0

    monkeypatch.setenv("AGENT_LLM_READ_TIMEOUT", "off")
    assert resolve_read_timeout(DEFAULT_READ_TIMEOUT) is None

    monkeypatch.setenv("AGENT_LLM_READ_TIMEOUT", "0")
    assert resolve_read_timeout(DEFAULT_READ_TIMEOUT) is None

    monkeypatch.setenv("AGENT_LLM_READ_TIMEOUT", "not-a-number")
    assert resolve_read_timeout(DEFAULT_READ_TIMEOUT) == DEFAULT_READ_TIMEOUT


def test_openai_compatible_defaults_to_a_finite_read_timeout() -> None:
    provider = OpenAICompatibleProvider(
        base_url="https://example.com",
        api_key="sk-test",
        model="gpt-4o",
    )

    assert provider.read_timeout == DEFAULT_READ_TIMEOUT


def test_a_pre_token_timeout_is_not_retriable() -> None:
    assert _is_retriable(PrefillTimeoutError("still prefilling")) is False
    assert _is_retriable(httpx.ReadTimeout("dropped")) is True


class _SilentStream(httpx.AsyncByteStream):
    """Never emits a line: models the silent prefill before the first token."""

    def __init__(self) -> None:
        self.closed = asyncio.Event()

    async def __aiter__(self):
        await self.closed.wait()
        return
        yield b""  # pragma: no cover - makes this an async generator

    async def aclose(self) -> None:
        self.closed.set()


@pytest.mark.asyncio
async def test_openai_compatible_cancellation_interrupts_a_silent_prefill() -> None:
    """Escape must still abort while the server is silently prefilling.

    With no read timeout a silent stream would otherwise never reach the
    per-line cancellation check, so cancellation has to close the response.
    """
    silent = _SilentStream()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=silent,
        )

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(
        base_url="http://127.0.0.1:11500",
        transport=transport,
        timeout=httpx.Timeout(120.0, connect=10.0, read=None),
    )
    provider = OpenAICompatibleProvider(
        base_url="http://127.0.0.1:11500",
        api_key="",
        model="some/local-model",
        http_client=client,
    )

    cancel_event = asyncio.Event()
    stream = provider.stream([Message.user("hi")], options=StreamOptions(cancel_event=cancel_event))

    async def cancel_soon() -> None:
        await asyncio.sleep(0.05)
        cancel_event.set()

    result = await asyncio.gather(stream.result(), cancel_soon())

    await provider.client.aclose()

    assert silent.closed.is_set()
    assert result[0].stop_reason == "aborted"
