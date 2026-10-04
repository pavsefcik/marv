"""Behavior tests for the tool approval policy."""

from __future__ import annotations

import pytest

from marv.config import Config
from marv.runtime.agent import Agent
from marv.runtime.approval import ApprovalMode, is_destructive
from tests.test_doubles.llm_provider_fake import LLMProviderFake
from tests.test_doubles.llm_stream_builders import make_text_events, make_tool_call_events


class RecordingApprover:
    """Approver double that records requests and returns a fixed decision."""

    def __init__(self, allow: bool) -> None:
        self.allow = allow
        self.requests: list[object] = []

    async def approve(self, request: object) -> bool:
        self.requests.append(request)
        return self.allow


def build_agent(temp_dir, scripts, *, approval_mode: ApprovalMode, approver=None):
    config = Config(
        provider="fake",
        model="fake-model",
        api_key="test",
        session_dir=temp_dir,
        context_max_tokens=2048,
        max_output_tokens=2048,
        approval_mode=approval_mode,
    )
    provider = LLMProviderFake(scripts, name="fake", model="fake-model")
    return Agent(config.to_agent_settings(), provider, approver=approver)


async def run_tool(agent, prompt="go"):
    return [chunk async for chunk in agent.run(prompt)]


def tool_results(chunks):
    return [chunk.payload for chunk in chunks if chunk.type == "tool_result"]


@pytest.mark.parametrize(
    ("tool_name", "arguments", "expected"),
    [
        ("write", {"path": "x"}, True),
        ("edit", {"path": "x"}, True),
        ("read", {"path": "x"}, False),
        ("ls", {"path": "."}, False),
        ("bash", {"command": "ls -la"}, False),
        ("bash", {"command": "git status"}, False),
        ("bash", {"command": "git push origin main"}, True),
        ("bash", {"command": "rm -rf build"}, True),
        ("bash", {"command": "sudo mv a b"}, True),
        ("bash", {"command": "echo hi > out.txt"}, True),
        ("bash", {"command": "python -c 'x' 2>&1"}, False),
        ("bash", {"command": "bash -c 'rm -rf build'"}, True),
        ("bash", {"command": "sh -c 'ls'"}, False),
        ("bash", {"command": "sed -i 's/a/b/' file.txt"}, True),
        ("bash", {"command": "find . -name '*.tmp' -delete"}, True),
        ("bash", {"command": "echo hi | tee out.txt"}, True),
    ],
)
def test_is_destructive_classification(tool_name, arguments, expected):
    assert is_destructive(tool_name, arguments) is expected


@pytest.mark.asyncio
async def test_off_mode_runs_destructive_tool_without_approver(temp_dir):
    target = temp_dir / "note.txt"
    scripts = [
        make_tool_call_events("c1", "write", {"path": str(target), "content": "hi"}),
        make_text_events("done"),
    ]
    agent = build_agent(temp_dir, scripts, approval_mode=ApprovalMode.OFF)

    chunks = await run_tool(agent)

    assert target.read_text() == "hi"
    assert not tool_results(chunks)[0].result.startswith("Tool denied")


@pytest.mark.asyncio
async def test_destructive_mode_runs_tool_when_approved(temp_dir):
    target = temp_dir / "note.txt"
    scripts = [
        make_tool_call_events("c1", "write", {"path": str(target), "content": "hi"}),
        make_text_events("done"),
    ]
    approver = RecordingApprover(allow=True)
    agent = build_agent(
        temp_dir, scripts, approval_mode=ApprovalMode.DESTRUCTIVE, approver=approver
    )

    chunks = await run_tool(agent)

    assert target.read_text() == "hi"
    assert len(approver.requests) == 1
    assert "denied" not in tool_results(chunks)[0].result


@pytest.mark.asyncio
async def test_destructive_mode_denies_when_user_rejects(temp_dir):
    target = temp_dir / "note.txt"
    scripts = [
        make_tool_call_events("c1", "write", {"path": str(target), "content": "hi"}),
        make_text_events("done"),
    ]
    approver = RecordingApprover(allow=False)
    agent = build_agent(
        temp_dir, scripts, approval_mode=ApprovalMode.DESTRUCTIVE, approver=approver
    )

    chunks = await run_tool(agent)

    assert not target.exists()
    assert "not approved" in tool_results(chunks)[0].result


@pytest.mark.asyncio
async def test_destructive_mode_denies_without_approver(temp_dir):
    target = temp_dir / "note.txt"
    scripts = [
        make_tool_call_events("c1", "write", {"path": str(target), "content": "hi"}),
        make_text_events("done"),
    ]
    agent = build_agent(temp_dir, scripts, approval_mode=ApprovalMode.DESTRUCTIVE)

    chunks = await run_tool(agent)

    assert not target.exists()
    assert "no approver is available" in tool_results(chunks)[0].result


@pytest.mark.asyncio
async def test_destructive_mode_skips_approval_for_read_only_tool(temp_dir):
    target = temp_dir / "note.txt"
    target.write_text("alpha")
    scripts = [
        make_tool_call_events("c1", "read", {"path": str(target)}),
        make_text_events("done"),
    ]
    approver = RecordingApprover(allow=False)
    agent = build_agent(
        temp_dir, scripts, approval_mode=ApprovalMode.DESTRUCTIVE, approver=approver
    )

    chunks = await run_tool(agent)

    assert "alpha" in tool_results(chunks)[0].result
    assert approver.requests == []


@pytest.mark.asyncio
async def test_all_mode_requires_approval_for_read_only_tool(temp_dir):
    target = temp_dir / "note.txt"
    target.write_text("alpha")
    scripts = [
        make_tool_call_events("c1", "read", {"path": str(target)}),
        make_text_events("done"),
    ]
    approver = RecordingApprover(allow=False)
    agent = build_agent(temp_dir, scripts, approval_mode=ApprovalMode.ALL, approver=approver)

    chunks = await run_tool(agent)

    assert len(approver.requests) == 1
    assert "denied" in tool_results(chunks)[0].result
