"""Behavior tests for host memory readings shown in the TUI status bar."""

from __future__ import annotations

import os

import pytest

from marv.config import Config
from marv.runtime.session import Session
from marv.tui.app import AgentApp
from marv.tui.memory import (
    MemoryUsage,
    format_bytes,
    read_available_bytes,
    read_process_tree_memory,
    snapshot,
)
from marv.tui.status import StatusBar
from tests.test_doubles.llm_provider_fake import LLMProviderFake

VM_STAT = """Mach Virtual Memory Statistics: (page size of 16384 bytes)
Pages free:                                     1000.
Pages active:                                   4000.
Pages inactive:                                 2000.
Pages speculative:                               500.
Pages purgeable:                                 250.
"""

PS_TREE = """\
    1     0  1000
  100     1  2000
  101   100  3000
  102   100  4000
  200     1  9999
"""


def test_available_memory_counts_reclaimable_pages():
    """available = (free + inactive + speculative + purgeable) * page size."""
    import marv.tui.memory as memory

    original = memory._run
    memory._run = lambda argv: VM_STAT if argv[0] == "vm_stat" else ""
    try:
        assert read_available_bytes() == (1000 + 2000 + 500 + 250) * 16384
    finally:
        memory._run = original


def test_process_tree_rss_sums_the_root_and_its_children_only():
    import marv.tui.memory as memory

    original = memory._run
    # No `top` output: fall back to the ps RSS column.
    memory._run = lambda argv: PS_TREE if argv[0] == "ps" else ""
    try:
        assert read_process_tree_memory(100) == (2000 + 3000 + 4000) * 1024
    finally:
        memory._run = original


def test_process_tree_prefers_footprints_over_ps_rss():
    """The footprint (Metal-wired memory included) wins over ps RSS."""
    import marv.tui.memory as memory

    original = memory._run
    top = "PID    MEM  \n  100  5.8G  \n  101  200M  \n  102  300M  \n  200  999M  \n"
    memory._run = lambda argv: PS_TREE if argv[0] == "ps" else top
    try:
        expected = int(5.8 * 1024**3) + 200 * 1024**2 + 300 * 1024**2
        assert read_process_tree_memory(100) == expected
    finally:
        memory._run = original


def test_snapshot_counts_a_server_that_is_not_a_child(monkeypatch):
    import marv.tui.memory as memory

    table = f"  {os.getpid()}     1  2000\n   4242     1  9999\n"
    monkeypatch.setattr(memory, "_run", lambda argv: table)

    assert snapshot(server_pid=4242).marv == (2000 + 9999) * 1024


def test_snapshot_does_not_double_count_a_child_server(monkeypatch):
    import marv.tui.memory as memory

    # The server is pid 4242, a child of this very process: walked once.
    table = f"  {os.getpid()}     1  2000\n   4242  {os.getpid()}  9999\n"
    monkeypatch.setattr(memory, "_run", lambda argv: table)

    assert snapshot(server_pid=4242).marv == (2000 + 9999) * 1024


def test_formatting_is_compact_and_readable():
    assert format_bytes(19_327_352_832) == "18.0G"
    assert format_bytes(6 * 1024**3 + 400 * 1024**2) == "6.4G"
    assert format_bytes(512 * 1024**2) == "512M"


def test_label_reports_marv_total_and_available():
    usage = MemoryUsage(
        marv=6 * 1024**3,
        available=9 * 1024**3,
        total=18 * 1024**3,
    )
    assert usage.label() == "RAM 6.0G/18.0G (9.0G free)"


@pytest.mark.asyncio
async def test_tui_runner_status_displays_ram_usage(temp_dir, monkeypatch):
    import marv.tui.app as app_module

    monkeypatch.setattr(
        app_module,
        "snapshot",
        lambda server_pid=None: MemoryUsage(
            marv=6 * 1024**3, available=9 * 1024**3, total=18 * 1024**3
        ),
    )
    session = Session.new(temp_dir)
    app = AgentApp(
        Config(provider="openai", model="gpt-4o", api_key="test", session_dir=temp_dir),
        provider=LLMProviderFake([], name="openai", model="gpt-4o"),
        session=session,
    )

    async with app.run_test() as pilot:
        await pilot.pause()

        status = app.query_one("#status-line", StatusBar)
        status.set_memory(app_module.snapshot())
        left = app.query_one("#status-left").render()
        text = getattr(left, "plain", str(left))
        assert "RAM 6.0G/18.0G (9.0G free)" in text


@pytest.mark.asyncio
async def test_status_bar_hides_ram_when_total_is_unknown(temp_dir):
    session = Session.new(temp_dir)
    app = AgentApp(
        Config(provider="openai", model="gpt-4o", api_key="test", session_dir=temp_dir),
        provider=LLMProviderFake([], name="openai", model="gpt-4o"),
        session=session,
    )

    async with app.run_test() as pilot:
        await pilot.pause()

        status = app.query_one("#status-line", StatusBar)
        status.set_memory(MemoryUsage(marv=0, available=0, total=0))
        left = app.query_one("#status-left").render()
        text = getattr(left, "plain", str(left))
        assert "RAM" not in text
