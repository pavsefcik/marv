"""Extension system for runtime customization.

Extensions can:
- Hook into agent lifecycle events
- Block or modify tool calls
- Transform messages before LLM calls
- Register custom tools and commands
"""

from marv.extensions.api import (
    UIAPI,
    ExtensionAPI,
    ExtensionContext,
    ExtensionUIBindings,
    ModelAPI,
    PresentedView,
    RuntimeAPI,
    SessionAPI,
    ToolsAPI,
    ViewControl,
    WidgetView,
)
from marv.extensions.host import ExtensionHost
from marv.extensions.loader import ExtensionLoader
from marv.extensions.runner import ExtensionRunner
from marv.extensions.types import (
    INTERNAL_EXTENSION_EVENTS,
    PUBLIC_EXTENSION_EVENTS,
    ContextEvent,
    InputEvent,
    ToolCallEvent,
    ToolCallResult,
    ToolResultEvent,
    ToolResultModification,
)

__all__ = [
    "ExtensionAPI",
    "ExtensionContext",
    "ExtensionHost",
    "ExtensionUIBindings",
    "PresentedView",
    "ViewControl",
    "RuntimeAPI",
    "SessionAPI",
    "ModelAPI",
    "ToolsAPI",
    "UIAPI",
    "WidgetView",
    "ExtensionLoader",
    "ExtensionRunner",
    "PUBLIC_EXTENSION_EVENTS",
    "INTERNAL_EXTENSION_EVENTS",
    "ContextEvent",
    "InputEvent",
    "ToolCallEvent",
    "ToolCallResult",
    "ToolResultEvent",
    "ToolResultModification",
]
