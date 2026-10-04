"""marv - A local-first coding agent TUI for Apple Silicon."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("marv")
except PackageNotFoundError:  # pragma: no cover - source tree without an install
    __version__ = "0.0.0"

from marv.config import Config
from marv.runtime.agent import Agent
from marv.runtime.message import Message, Role, ToolCall
from marv.runtime.session import Session

__all__ = [
    "Agent",
    "Config",
    "Message",
    "Role",
    "Session",
    "ToolCall",
]
