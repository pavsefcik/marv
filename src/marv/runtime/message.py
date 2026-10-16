"""Message types for the conversation history."""

import json
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Self
from uuid import uuid4

from pydantic import BaseModel, Field

#: Rough token budget charged for one image attachment when estimating a
#: prompt. The vision encoder turns an image into a fixed grid of patches, so
#: the real cost is only loosely tied to the byte size; this constant exists so
#: a screenshot cannot make the context budget look free.
IMAGE_TOKEN_COST = 1024


class Role(StrEnum):
    """Message roles following OpenAI convention."""

    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


class ImageAttachment(BaseModel):
    """An image (or rendered PDF page) carried with a user message.

    The base64 ``data`` is in-memory only: it is excluded from serialization so
    a session JSONL never carries megabytes of image bytes. A persisted
    attachment is rehydrated from ``ref`` — a filename under the session's
    content-addressed ``attachments/`` blob directory.
    """

    media_type: str  # "image/png", "image/jpeg", ...
    data: str = Field(default="", exclude=True)  # base64, no `data:` prefix
    name: str = ""  # original filename, for display
    byte_size: int = 0
    ref: str | None = None  # blob filename under the attachments directory


class ToolCall(BaseModel):
    """A tool call from the assistant."""

    id: str
    name: str
    arguments: dict[str, Any]

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> Self:
        """Parse from OpenAI API format."""
        args = data.get("function", {}).get("arguments", "{}")
        if isinstance(args, str):
            args = json.loads(args) if args else {}
        return cls(
            id=data.get("id", uuid4().hex[:12]),
            name=data.get("function", {}).get("name", ""),
            arguments=args,
        )

    def to_api_dict(self) -> dict[str, Any]:
        """Convert to OpenAI API format."""
        return {
            "id": self.id,
            "type": "function",
            "function": {
                "name": self.name,
                "arguments": json.dumps(self.arguments),
            },
        }


class ToolResult(BaseModel):
    """Result from executing a tool."""

    tool_call_id: str
    name: str
    result: str
    is_error: bool = False


class ToolCallStart(BaseModel):
    """Notification that a tool call is starting to stream.

    Emitted when toolcall_start event is received, before arguments
    are fully parsed. Allows UI to show tool name immediately.
    """

    id: str
    name: str


class ThinkingContent(BaseModel):
    """Thinking/reasoning block from LLM.

    Represents model reasoning that may be shown to users but is not
    part of the main response. Used by models with extended thinking
    and O1/O3 (reasoning effort).
    """

    text: str = ""


class Message(BaseModel):
    """A message in the conversation."""

    id: str = Field(default_factory=lambda: uuid4().hex[:12])
    role: Role
    content: str
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None  # For tool results
    thinking: ThinkingContent | None = None  # For reasoning/thinking content
    #: Images sent alongside a user message. Only user messages carry them;
    #: other roles ignore the field when serializing.
    attachments: list[ImageAttachment] = Field(default_factory=list)
    provider_metadata: dict[str, Any] | None = None  # Provider-specific artifacts
    provider: str | None = None  # Provider name for assistant messages
    model: str | None = None  # Model ID for assistant messages
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    parent_id: str | None = None  # For branching

    def to_api_dict(self) -> dict[str, Any]:
        """Convert to OpenAI API format.

        A user message with attachments becomes a list of content parts (text
        then one `image_url` per image); every other message stays a plain
        string, so attachments on a non-user role are ignored.
        """
        content: str | list[dict[str, Any]] = self.content
        # An attachment whose bytes could not be rehydrated carries empty data;
        # skip it rather than emitting a broken data URL.
        images = [a for a in self.attachments if a.data]
        if self.role is Role.USER and images:
            parts: list[dict[str, Any]] = []
            if self.content:
                parts.append({"type": "text", "text": self.content})
            parts.extend(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{a.media_type};base64,{a.data}"},
                }
                for a in images
            )
            content = parts

        d: dict[str, Any] = {"role": self.role.value, "content": content}

        if self.tool_calls:
            d["tool_calls"] = [tc.to_api_dict() for tc in self.tool_calls]

        if self.tool_call_id:
            d["tool_call_id"] = self.tool_call_id

        return d

    @classmethod
    def user(cls, content: str, attachments: list[ImageAttachment] | None = None) -> Self:
        """Create a user message, optionally carrying image attachments."""
        return cls(role=Role.USER, content=content, attachments=attachments or [])

    @classmethod
    def assistant(
        cls,
        content: str,
        tool_calls: list[ToolCall] | None = None,
        thinking: ThinkingContent | None = None,
    ) -> Self:
        """Create an assistant message."""
        return cls(role=Role.ASSISTANT, content=content, tool_calls=tool_calls, thinking=thinking)

    @classmethod
    def system(cls, content: str) -> Self:
        """Create a system message."""
        return cls(role=Role.SYSTEM, content=content)

    @classmethod
    def tool_result(cls, tool_call_id: str, content: str) -> Self:
        """Create a tool result message."""
        return cls(role=Role.TOOL, content=content, tool_call_id=tool_call_id)
