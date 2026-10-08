"""OpenAI-compatible LLM provider with event-based streaming.

Works with: Ollama, LM Studio, OpenRouter, Groq, Together, Mistral, etc.

This is a simplified provider that focuses on compatibility rather than
advanced features like reasoning_effort. Use openai.py for native OpenAI.

Supports:
- Basic tool use with tool_choice modes
- Request cancellation and retry with exponential backoff
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

import httpx
import tiktoken

from marv.llm.events import (
    AssistantMetadataEvent,
    DoneEvent,
    ErrorEvent,
    PartialMessage,
    StartEvent,
    StopReason,
    StreamOptions,
    TextBlock,
    TextDeltaEvent,
    TextEndEvent,
    TextStartEvent,
    ThinkingDeltaEvent,
    ThinkingEndEvent,
    ThinkingStartEvent,
    ToolCallBlock,
    ToolCallDeltaEvent,
    ToolCallEndEvent,
    ToolCallStartEvent,
    Usage,
)
from marv.llm.retry import RetryConfig, with_retry
from marv.llm.stream import AssistantMessageEventStream

if TYPE_CHECKING:
    from marv.runtime.message import Message


class LLMError(Exception):
    """Error from the LLM API with parsed message."""

    def __init__(self, message: str, status_code: int | None = None):
        self.message = message
        self.status_code = status_code
        super().__init__(message)


class PrefillTimeoutError(LLMError):
    """The read timeout expired before the server sent a single token.

    For a local model this means the prompt is still being prefilled, not that
    the transport failed transiently. Retrying would restart the whole prefill
    from zero, so this error is explicitly non-retriable: it is surfaced to the
    user (who can raise ``AGENT_LLM_READ_TIMEOUT`` or shrink the prompt).
    """

    retriable = False


#: Read timeout for the generic OpenAI-compatible transport. Cloud endpoints
#: keep an SSE connection warm, so a two-minute silence is a real failure there.
DEFAULT_READ_TIMEOUT = 120.0

#: Read timeout for a marv-launched local server. A long-prompt prefill on
#: Apple Silicon can legitimately run for minutes before the first token, and
#: ``mlx_vlm`` sends nothing on the stream while it works, so there is no
#: default read timeout; cancellation and server teardown still apply.
DEFAULT_LOCAL_READ_TIMEOUT: float | None = None

#: Seconds to wait for a TCP connection to establish.
DEFAULT_CONNECT_TIMEOUT = 10.0


def resolve_read_timeout(default: float | None) -> float | None:
    """Resolve the streaming read timeout from ``AGENT_LLM_READ_TIMEOUT``.

    Overrides apply to every provider. The value is in seconds; ``0``, ``none``,
    ``off`` or ``disabled`` disables the timeout entirely (wait as long as the
    server needs). An unparseable value falls back to ``default``.
    """
    raw = os.environ.get("AGENT_LLM_READ_TIMEOUT")
    if raw is None:
        return default
    text = raw.strip().lower()
    if text in {"", "none", "off", "disabled", "infinite"}:
        return None
    try:
        value = float(text)
    except ValueError:
        return default
    return value if value > 0 else None


def _parse_api_error(response: httpx.Response) -> str:
    """Extract error message from API response."""
    try:
        data = response.json()
        if "error" in data:
            error = data["error"]
            if isinstance(error, dict):
                return str(error.get("message", error))
            return str(error)
        return response.text or f"HTTP {response.status_code}"
    except Exception:
        return response.text or f"HTTP {response.status_code}"


def _parse_stream_error(error: object) -> str:
    """Normalize an error payload embedded in a streaming chunk.

    OpenAI-style errors are ``{"message": ...}`` objects; the mlx_vlm server
    emits a bare string. Both end up as a readable message.
    """
    if isinstance(error, dict):
        return str(error.get("message") or error)
    return str(error)


#: Rough characters-per-token ratio used for the latency metadata's prompt
#: estimate. Exact per-model tokenization is unnecessary here: the number only
#: has to be stable and comparable turn to turn, which is what a regression
#: check needs.
_CHARS_PER_TOKEN = 4


def _estimate_payload_tokens(payload_messages: list[dict[str, Any]]) -> int:
    """Estimate prompt tokens from the serialized message payload."""
    return len(json.dumps(payload_messages)) // _CHARS_PER_TOKEN


def _map_stop_reason(openai_reason: str | None) -> StopReason:
    """Map OpenAI finish reason to our StopReason type."""
    mapping: dict[str | None, StopReason] = {
        "stop": "stop",
        "tool_calls": "tool_use",
        "length": "length",
        "content_filter": "stop",
        None: "stop",
    }
    return mapping.get(openai_reason, "stop")


@dataclass(slots=True)
class CompatSettings:
    """Provider-specific compatibility settings."""

    max_tokens_field: Literal["max_tokens", "max_completion_tokens"] = "max_tokens"
    supports_developer_role: bool = False
    requires_tool_result_name: bool = False
    requires_thinking_as_text: bool = False
    tool_id_max_length: int | None = None  # Some providers limit tool ID length


def _detect_compat_settings(base_url: str) -> CompatSettings:
    """Auto-detect compatibility settings from base URL."""
    url_lower = base_url.lower()

    # Mistral quirks
    if "mistral.ai" in url_lower:
        return CompatSettings(
            tool_id_max_length=9,  # Mistral limits tool IDs to 9 chars
            requires_thinking_as_text=True,
        )

    # Groq defaults
    if "groq.com" in url_lower:
        return CompatSettings()

    # OpenRouter defaults
    if "openrouter.ai" in url_lower:
        return CompatSettings()

    # Local providers (Ollama, LM Studio)
    if "localhost" in url_lower or "127.0.0.1" in url_lower:
        return CompatSettings()

    # Default settings
    return CompatSettings()


@dataclass(slots=True)
class OpenAICompatibleProvider:
    """Provider for any OpenAI-compatible API."""

    base_url: str
    api_key: str
    model: str
    name: str = "openai-compat"
    temperature: float = 0.7
    max_tokens: int = 4096
    retry_config: RetryConfig = field(default_factory=RetryConfig, repr=False)
    #: Read timeout in seconds; ``None`` waits indefinitely (local servers).
    #: Resolved through :func:`resolve_read_timeout` in ``__post_init__``.
    read_timeout: float | None = DEFAULT_READ_TIMEOUT
    http_client: httpx.AsyncClient | None = field(default=None, repr=False)
    enable_thinking: bool | None = field(default=None, repr=False)
    thinking_start_token: str | None = field(default=None, repr=False)
    thinking_end_token: str | None = field(default=None, repr=False)
    _client: httpx.AsyncClient | None = field(default=None, repr=False)
    _encoder: tiktoken.Encoding | None = field(default=None, repr=False)
    _compat: CompatSettings | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        self.read_timeout = resolve_read_timeout(self.read_timeout)
        if self.http_client is not None:
            self._client = self.http_client

    @property
    def client(self) -> httpx.AsyncClient:
        """Get or create the HTTP client."""
        if self._client is None:
            headers = {"Content-Type": "application/json"}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            self._client = httpx.AsyncClient(
                base_url=self.base_url.rstrip("/"),
                headers=headers,
                timeout=httpx.Timeout(
                    DEFAULT_READ_TIMEOUT,
                    connect=DEFAULT_CONNECT_TIMEOUT,
                    read=self.read_timeout,
                ),
            )
        return self._client

    @property
    def encoder(self) -> tiktoken.Encoding:
        """Get or create the token encoder."""
        if self._encoder is None:
            try:
                self._encoder = tiktoken.encoding_for_model(self.model)
            except KeyError:
                # Fall back to cl100k_base for unknown models
                self._encoder = tiktoken.get_encoding("cl100k_base")
        return self._encoder

    @property
    def compat(self) -> CompatSettings:
        """Get compatibility settings."""
        if self._compat is None:
            self._compat = _detect_compat_settings(self.base_url)
        return self._compat

    def set_model(self, model: str) -> None:
        """Update model and clear model-scoped caches."""
        from marv.llm.models import is_model_valid_for_provider

        if not model or model == self.model:
            return
        if not is_model_valid_for_provider(model, self.name):
            raise ValueError(f"Model '{model}' is not valid for provider '{self.name}'")
        self.model = model
        self._encoder = None

    def _build_payload(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None,
        options: StreamOptions | None,
    ) -> dict[str, Any]:
        """Build the API request payload."""
        max_tokens = options.max_tokens if options and options.max_tokens else self.max_tokens
        temp = (
            options.temperature if options and options.temperature is not None else self.temperature
        )

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [m.to_api_dict() for m in messages],
            "stream": True,
            self.compat.max_tokens_field: max_tokens,
            "temperature": temp,
        }

        # Thinking control for providers that pass it through (the mlx_vlm
        # server honors enable_thinking and optional bracket markers).
        if self.enable_thinking is not None:
            payload["enable_thinking"] = bool(self.enable_thinking)
        if self.thinking_start_token:
            payload["thinking_start_token"] = self.thinking_start_token
        if self.thinking_end_token:
            payload["thinking_end_token"] = self.thinking_end_token

        if tools:
            # Apply tool ID length limit if needed
            if self.compat.tool_id_max_length:
                tools = self._truncate_tool_ids(tools)
            payload["tools"] = tools
            # Tool choice handling (OpenAI format)
            if options and options.tool_choice:
                tc = options.tool_choice
                if tc == "any":
                    # OpenAI uses "required" for "must use a tool"
                    payload["tool_choice"] = "required"
                elif tc == "none":
                    # Remove tools entirely when none
                    payload.pop("tools", None)
                elif isinstance(tc, dict) and "name" in tc:
                    # Specific tool
                    payload["tool_choice"] = {
                        "type": "function",
                        "function": {"name": tc["name"]},
                    }
                else:
                    # "auto", "required", etc. pass through
                    payload["tool_choice"] = tc
            else:
                payload["tool_choice"] = "auto"

        return payload

    def _truncate_tool_ids(self, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Truncate tool IDs if provider requires it."""
        # This is mainly for Mistral which has a 9-char limit
        # The actual truncation happens when processing tool calls
        return tools

    def stream(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        options: StreamOptions | None = None,
    ) -> AssistantMessageEventStream:
        """Stream completion as events.

        Returns an AssistantMessageEventStream that yields events
        and provides final PartialMessage via .result().
        """
        stream = AssistantMessageEventStream()
        stream.attach_task(asyncio.create_task(self._stream_impl(messages, tools, options, stream)))
        return stream

    async def _stream_impl(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None,
        options: StreamOptions | None,
        stream: AssistantMessageEventStream,
    ) -> None:
        """Internal implementation of streaming with retry and cancellation."""
        output = PartialMessage()

        # Track state
        text_started = False
        text_content = ""
        thinking_started = False
        thinking_content = ""
        pending_tool_calls: dict[int, ToolCallBlock] = {}
        finish_reason: str | None = None
        saw_done = False

        # Latency instrumentation. TTFT is measured from just before the
        # request is handed to the transport to the first decoded token, which
        # is the delay the user actually perceives. Token counts are emitted
        # alongside it so a rising TTFT can be attributed to prompt growth
        # (prefill) rather than to the transport.
        request_sent_at: float | None = None
        first_token_at: float | None = None
        token_events = 0
        prompt_tokens_est = 0
        schema_tokens_est = 0 if not tools else len(json.dumps(tools)) // _CHARS_PER_TOKEN

        def _note_first_token() -> None:
            nonlocal first_token_at, token_events
            token_events += 1
            if first_token_at is None:
                first_token_at = time.monotonic()

        def _latency_metadata() -> dict[str, Any]:
            """Metadata dict describing this turn's latency."""
            streamed = first_token_at is not None and request_sent_at is not None
            ttft_ms = (
                max(0.0, (first_token_at - request_sent_at) * 1000.0)
                if streamed and first_token_at is not None and request_sent_at is not None
                else 0.0
            )
            return {
                "ttft_ms": ttft_ms,
                "prompt_tokens": prompt_tokens_est,
                "schema_tokens": schema_tokens_est,
                "streamed": streamed,
                # Number of decoded stream events (a proxy for output length).
                "token_events": token_events,
            }

        def _check_cancelled() -> bool:
            """Check if request was cancelled."""
            return bool(options and options.cancel_event and options.cancel_event.is_set())

        def _stream_thinking(text: str) -> None:
            """Emit thinking events from a reasoning delta, if any."""
            nonlocal thinking_started, thinking_content
            if text:
                if not thinking_started:
                    thinking_started = True
                    stream.push(ThinkingStartEvent(content_index=0))
                thinking_content += text
                stream.push(ThinkingDeltaEvent(content_index=0, delta=text))

        async def _do_stream_once() -> None:
            """Execute the streaming request (for retry)."""
            nonlocal text_started, text_content, pending_tool_calls, finish_reason, saw_done
            nonlocal request_sent_at, prompt_tokens_est, first_token_at

            # Check cancellation before starting
            if _check_cancelled():
                raise asyncio.CancelledError("Request cancelled")

            payload = self._build_payload(messages, tools, options)
            prompt_tokens_est = _estimate_payload_tokens(payload.get("messages", []))

            # Emit start event
            stream.push(StartEvent(partial=output))
            # Each attempt is timed independently: a retry must not inherit the
            # previous attempt's first-token timestamp.
            first_token_at = None
            request_sent_at = time.monotonic()

            async with self.client.stream(
                "POST",
                "/v1/chat/completions",
                json=payload,
            ) as response:
                if response.status_code >= 400:
                    await response.aread()
                    error_msg = _parse_api_error(response)
                    raise LLMError(error_msg, response.status_code)

                # With no read timeout (local servers), a silent prefill emits
                # no lines, so the per-line cancellation check below would never
                # run. This watchdog closes the response the moment the request
                # is cancelled, which unblocks the line iterator.
                cancel_event = options.cancel_event if options else None
                watchdog: asyncio.Task[None] | None = None
                if cancel_event is not None:

                    async def _watch_cancel() -> None:
                        await cancel_event.wait()
                        await response.aclose()

                    watchdog = asyncio.create_task(_watch_cancel())

                try:
                    async for line in response.aiter_lines():
                        # Check cancellation during streaming
                        if _check_cancelled():
                            stream.abort("Request cancelled")
                            return

                        if not line.startswith("data: "):
                            continue

                        data_str = line[6:]  # Remove "data: " prefix
                        if data_str == "[DONE]":
                            saw_done = True
                            break

                        try:
                            chunk = json.loads(data_str)
                        except json.JSONDecodeError:
                            continue

                        # A server can report a failure as a top-level `error` in
                        # the SSE body after the HTTP 200 was already sent (the
                        # mlx_vlm server does this on Metal OOM). Raise so it takes
                        # the normal error path instead of looking like an empty,
                        # successful completion.
                        if chunk.get("error") is not None:
                            raise LLMError(_parse_stream_error(chunk["error"]))

                        # Handle usage if present
                        if chunk.get("usage"):
                            usage_data = chunk["usage"]
                            output.usage = Usage(
                                input=usage_data.get("prompt_tokens", 0),
                                output=usage_data.get("completion_tokens", 0),
                            )
                            output.usage.update_total()

                        choices = chunk.get("choices", [])
                        if not choices:
                            continue

                        choice = choices[0]
                        delta = choice.get("delta", {})

                        # Check finish reason
                        if choice.get("finish_reason"):
                            finish_reason = choice["finish_reason"]

                        # mlx_vlm routes reasoning to `reasoning_content` (or
                        # aliases). Emit it as thinking events independent of text.
                        reasoning = delta.get("reasoning_content") or delta.get("reasoning") or ""
                        if reasoning:
                            _stream_thinking(reasoning)
                            _note_first_token()

                        # Handle content tokens
                        if content := delta.get("content"):
                            _note_first_token()
                            if not text_started:
                                text_started = True
                                output.content.append(TextBlock(text=""))
                                stream.push(TextStartEvent(content_index=len(output.content) - 1))
                            text_content += content
                            if output.content and isinstance(output.content[-1], TextBlock):
                                output.content[-1].text = text_content
                            stream.push(
                                TextDeltaEvent(content_index=len(output.content) - 1, delta=content)
                            )

                        # Handle tool calls
                        if tool_calls := delta.get("tool_calls"):
                            for tc in tool_calls:
                                idx = tc.get("index", 0)

                                if idx not in pending_tool_calls:
                                    # New tool call
                                    tool_id = tc.get("id", "")
                                    # Truncate ID if needed
                                    if (
                                        self.compat.tool_id_max_length
                                        and len(tool_id) > self.compat.tool_id_max_length
                                    ):
                                        tool_id = tool_id[: self.compat.tool_id_max_length]

                                    tool_block = ToolCallBlock(
                                        id=tool_id,
                                        name=tc.get("function", {}).get("name", ""),
                                    )
                                    pending_tool_calls[idx] = tool_block
                                    output.content.append(tool_block)
                                    stream.push(
                                        ToolCallStartEvent(
                                            content_index=len(output.content) - 1,
                                            tool_id=tool_block.id,
                                            tool_name=tool_block.name,
                                        )
                                    )

                                tool_block = pending_tool_calls[idx]

                                # Update ID if provided
                                if tc.get("id"):
                                    tool_id = tc["id"]
                                    if (
                                        self.compat.tool_id_max_length
                                        and len(tool_id) > self.compat.tool_id_max_length
                                    ):
                                        tool_id = tool_id[: self.compat.tool_id_max_length]
                                    tool_block.id = tool_id

                                # Update name if provided
                                if func := tc.get("function"):
                                    if func.get("name"):
                                        tool_block.name = func["name"]
                                    if func.get("arguments"):
                                        tool_block.append_arguments_delta(func["arguments"])
                                        stream.push(
                                            ToolCallDeltaEvent(
                                                content_index=len(output.content) - 1,
                                                delta=func["arguments"],
                                            )
                                        )
                finally:
                    if watchdog is not None:
                        watchdog.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await watchdog

                # Cancelling during a silent prefill closes the response, which
                # ends the line iterator cleanly. Surface the abort instead of
                # misreporting it as a truncated stream.
                if _check_cancelled():
                    stream.abort("Request cancelled")
                    return

        async def _do_stream() -> None:
            """Run one attempt, classifying a pre-token read timeout as fatal.

            A read timeout *before the first token* on a local model means the
            prefill is still running, not that the connection failed. Retrying
            would throw away minutes of GPU work and start over, so it is
            converted to a non-retriable :class:`PrefillTimeoutError` and
            surfaced instead. A timeout *after* tokens have arrived is a real
            transport failure and remains retriable.
            """
            try:
                await _do_stream_once()
            except httpx.ReadTimeout as e:
                if first_token_at is None:
                    raise PrefillTimeoutError(
                        "timed out before the first token: the prompt was still "
                        "being processed, and retrying would restart it from "
                        "scratch. Raise or disable AGENT_LLM_READ_TIMEOUT for a "
                        "local model."
                    ) from e
                raise

        try:
            await with_retry(_do_stream, self.retry_config)

            # Skip done event if already aborted
            if stream.is_aborted:
                return

            # A stream that ends without a finish reason or a [DONE] sentinel
            # was truncated (e.g. the server errored and closed mid-response).
            # Surface it as an error rather than a successful empty completion.
            if not saw_done and finish_reason is None:
                raise LLMError("stream closed before completion")

            # End text block if started
            if text_started:
                stream.push(TextEndEvent(content_index=len(output.content) - 1, text=text_content))

            # End thinking block if started
            if thinking_started:
                stream.push(ThinkingEndEvent(content_index=0, thinking=thinking_content))

            # Emit completed tool calls
            for idx in sorted(pending_tool_calls.keys()):
                tool_block = pending_tool_calls[idx]
                try:
                    raw_args = tool_block.arguments_raw_json
                    args = json.loads(raw_args) if raw_args else {}
                except json.JSONDecodeError:
                    args = {}
                tool_block.arguments = args
                stream.push(
                    ToolCallEndEvent(content_index=len(output.content) - 1, tool_call=tool_block)
                )

            # Set stop reason
            output.stop_reason = _map_stop_reason(finish_reason)

            # Publish latency metadata before completion so observers can
            # attribute this turn's TTFT before the run ends.
            stream.push(AssistantMetadataEvent(metadata={"latency": _latency_metadata()}))

            # Emit done event
            stream.push(DoneEvent(stop_reason=output.stop_reason, message=output))
            stream.end()

        except asyncio.CancelledError:
            output.error_message = "Request cancelled"
            output.stop_reason = "aborted"
            stream.push(ErrorEvent(stop_reason="aborted", message=output))
            stream.end(output)

        except Exception as e:
            output.error_message = str(e)
            output.stop_reason = "error"
            stream.push(ErrorEvent(stop_reason="error", message=output))
            stream.end(output)

    def count_tokens(self, text: str) -> int:
        """Count tokens using tiktoken."""
        return len(self.encoder.encode(text))

    def count_messages_tokens(self, messages: list[Message]) -> int:
        """Count tokens in a list of messages."""
        total = 0
        for msg in messages:
            total += 4  # Overhead per message
            total += self.count_tokens(msg.content)
            if msg.tool_calls:
                for tc in msg.tool_calls:
                    total += self.count_tokens(tc.name)
                    total += self.count_tokens(json.dumps(tc.arguments))
        return total

    async def list_models(self) -> list[str]:
        """Fetch available models from the API."""
        try:
            response = await self.client.get("/v1/models")
            response.raise_for_status()
            data = response.json()
            models = [m["id"] for m in data.get("data", [])]
            return sorted(models)
        except Exception:
            # Return empty list if API doesn't support /v1/models
            return []

    def supports_thinking(self) -> bool:
        """Check if the current model supports thinking/reasoning.

        Checks model registry first. For OpenAI-compatible providers,
        this may return True if proxying a reasoning-capable model
        (e.g., via OpenRouter).
        """
        from marv.llm.models import get_model_info, supports_reasoning

        # Check registry first (handles known models via OpenRouter etc.)
        info = get_model_info(self.model)
        if info is not None:
            return info.reasoning

        # Fallback: check if model name suggests reasoning support
        return supports_reasoning(self.model, provider="openai-compat")

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None
