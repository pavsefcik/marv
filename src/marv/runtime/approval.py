"""Tool approval policy: decide which tool calls need explicit consent.

The policy is off by default, so nothing changes unless it is opted into via
``approval_mode`` in config (or ``AGENT_APPROVAL``). It is intentionally a
heuristic: it is a safety net, not a sandbox.
"""

from __future__ import annotations

import re
import shlex
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Mapping

    from marv.runtime.hooks import ToolCallRequest


class ApprovalMode(StrEnum):
    """When tool calls require explicit approval."""

    OFF = "off"
    DESTRUCTIVE = "destructive"
    ALL = "all"


class ToolApprover(Protocol):
    """Asks the user (or a policy) whether a tool call may proceed."""

    async def approve(self, request: ToolCallRequest) -> bool: ...


#: Tools that mutate the workspace and are always treated as destructive.
DESTRUCTIVE_TOOLS = frozenset({"write", "edit"})

#: Command names that mutate state or are hard to undo.
_DESTRUCTIVE_COMMANDS = frozenset(
    {
        "rm",
        "rmdir",
        "mv",
        "dd",
        "truncate",
        "shred",
        "chmod",
        "chown",
        "chgrp",
        "tee",
        "mkfs",
        "fdisk",
        "kill",
        "pkill",
        "killall",
        "shutdown",
        "reboot",
        "sudo",
    }
)

#: `git` is only destructive for some subcommands.
_DESTRUCTIVE_GIT_SUBCOMMANDS = frozenset(
    {"push", "reset", "clean", "rebase", "checkout", "restore"}
)

#: Shells whose `-c` payload should be inspected recursively.
_SHELLS = frozenset({"bash", "sh", "zsh", "ksh", "dash"})

#: Shell constructs that write to files, substitute commands, or pipe into a shell.
_DESTRUCTIVE_PATTERNS = (
    re.compile(r"(^|[^0-9])>>?\s*[^\s&]"),  # output redirection to a file
    re.compile(r"\$\("),  # command substitution
    re.compile(r"`"),  # backtick substitution
    re.compile(r"\|\s*(?:ba|z|k)?sh\b"),  # piping into a shell
)


def is_destructive(tool_name: str, arguments: Mapping[str, object]) -> bool:
    """Return whether a tool call is considered destructive."""
    if tool_name in DESTRUCTIVE_TOOLS:
        return True
    if tool_name == "bash":
        return _bash_is_destructive(str(arguments.get("command", "")))
    return False


def requires_approval(
    mode: ApprovalMode,
    tool_name: str,
    arguments: Mapping[str, object],
) -> bool:
    """Return whether a tool call needs approval under the given mode."""
    if mode == ApprovalMode.OFF:
        return False
    if mode == ApprovalMode.ALL:
        return True
    return is_destructive(tool_name, arguments)


def describe_tool_call(request: ToolCallRequest) -> str:
    """Build a short human-readable summary of a tool call for a prompt."""
    if request.tool_name == "bash":
        command = str(request.input.get("command", "")).strip()
        return f"Run shell command?\n\n{command}"
    if request.tool_name in DESTRUCTIVE_TOOLS:
        path = str(request.input.get("path", "")).strip()
        return f"{request.tool_name.capitalize()} file?\n\n{path}"
    return f"Allow tool '{request.tool_name}'?"


def _bash_is_destructive(command: str) -> bool:
    if not command.strip():
        return False
    if any(pattern.search(command) for pattern in _DESTRUCTIVE_PATTERNS):
        return True

    tokens = _command_tokens(command)
    for index, token in enumerate(tokens):
        name = token.rsplit("/", 1)[-1]
        if name in _SHELLS and index + 1 < len(tokens) and tokens[index + 1] == "-c":
            payload = " ".join(tokens[index + 2 :])
            if payload and _bash_is_destructive(payload):
                return True
        elif name == "git":
            subcommand = tokens[index + 1] if index + 1 < len(tokens) else ""
            if subcommand in _DESTRUCTIVE_GIT_SUBCOMMANDS:
                return True
        elif (
            name in _DESTRUCTIVE_COMMANDS
            or (name == "find" and "-delete" in tokens)
            or (name == "sed" and any(t.startswith("-i") for t in tokens))
        ):
            return True
    return False


def _command_tokens(command: str) -> list[str]:
    try:
        return shlex.split(command)
    except ValueError:
        return command.split()
