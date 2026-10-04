"""LLM provider components."""

from marv.llm.apple_fm import AppleFMProvider
from marv.llm.events import (
    ContentBlock,
    Cost,
    DoneEvent,
    ErrorEvent,
    PartialMessage,
    StartEvent,
    StopReason,
    StreamEvent,
    StreamOptions,
    TextBlock,
    TextDeltaEvent,
    TextEndEvent,
    TextStartEvent,
    ThinkingBlock,
    ThinkingDeltaEvent,
    ThinkingEndEvent,
    ThinkingStartEvent,
    ToolCallBlock,
    ToolCallDeltaEvent,
    ToolCallEndEvent,
    ToolCallStartEvent,
    ToolChoice,
    Usage,
)
from marv.llm.factory import create_provider, resolve_provider_config
from marv.llm.marv_mlx import MarvMlxProvider
from marv.llm.openai_compat import LLMError, OpenAICompatibleProvider
from marv.llm.provider import LLMProvider
from marv.llm.retry import RetryConfig, with_retry
from marv.llm.stream import AssistantMessageEventStream, EventStream

__all__ = [
    # Providers
    "AppleFMProvider",
    "LLMError",
    "LLMProvider",
    "MarvMlxProvider",
    "OpenAICompatibleProvider",
    "create_provider",
    "resolve_provider_config",
    # Event stream
    "AssistantMessageEventStream",
    "EventStream",
    # Events
    "ContentBlock",
    "Cost",
    "DoneEvent",
    "ErrorEvent",
    "PartialMessage",
    "StartEvent",
    "StopReason",
    "StreamEvent",
    "StreamOptions",
    "TextBlock",
    "TextDeltaEvent",
    "TextEndEvent",
    "TextStartEvent",
    "ThinkingBlock",
    "ThinkingDeltaEvent",
    "ThinkingEndEvent",
    "ThinkingStartEvent",
    "ToolCallBlock",
    "ToolCallDeltaEvent",
    "ToolCallEndEvent",
    "ToolCallStartEvent",
    "ToolChoice",
    "Usage",
    # Retry
    "RetryConfig",
    "with_retry",
]
