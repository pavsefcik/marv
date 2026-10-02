"""Core agent components."""

from marv.runtime.agent import Agent
from marv.runtime.chunk import AgentChunk
from marv.runtime.context import ContextManager
from marv.runtime.events import (
    AgentEndEvent,
    AgentEvent,
    AgentStartEvent,
    ContextCompactionEvent,
    MessageEndEvent,
    MessageStartEvent,
    MessageUpdateEvent,
    ModelSelectEvent,
    ThinkingDeltaEvent,
    ThinkingEndEvent,
    ThinkingStartEvent,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    ToolExecutionUpdateEvent,
    TurnEndEvent,
    TurnStartEvent,
)
from marv.runtime.message import Message, Role, ThinkingContent, ToolCall, ToolResult
from marv.runtime.session import Session, SessionMetadata
from marv.runtime.settings import THINKING_BUDGETS, AgentSettings, ThinkingLevel

__all__ = [
    # Agent
    "Agent",
    # Runtime settings
    "AgentSettings",
    "ThinkingLevel",
    "THINKING_BUDGETS",
    # Context
    "ContextManager",
    # Agent chunks
    "AgentChunk",
    # Events
    "AgentEndEvent",
    "AgentEvent",
    "AgentStartEvent",
    "ContextCompactionEvent",
    "ModelSelectEvent",
    "MessageEndEvent",
    "MessageStartEvent",
    "MessageUpdateEvent",
    "ThinkingDeltaEvent",
    "ThinkingEndEvent",
    "ThinkingStartEvent",
    "ToolExecutionEndEvent",
    "ToolExecutionStartEvent",
    "ToolExecutionUpdateEvent",
    "TurnEndEvent",
    "TurnStartEvent",
    # Message
    "Message",
    "Role",
    "ThinkingContent",
    "ToolCall",
    "ToolResult",
    # Session
    "Session",
    "SessionMetadata",
]
