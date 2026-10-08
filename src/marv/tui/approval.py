"""TUI tool approver backed by the shared confirmation panel."""

from __future__ import annotations

from typing import TYPE_CHECKING

from marv.runtime.approval import describe_tool_call

if TYPE_CHECKING:
    from marv.runtime.hooks import ToolCallRequest
    from marv.tui.extension_bridge import TUIExtensionBridge


class TUIApprover:
    """Ask the user to approve a tool call through the confirm panel."""

    __slots__ = ("_bridge",)

    def __init__(self, bridge: TUIExtensionBridge) -> None:
        self._bridge = bridge

    async def approve(self, request: ToolCallRequest) -> bool:
        """Return whether the user approved the tool call."""
        return await self._bridge.confirm(describe_tool_call(request))
