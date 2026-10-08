"""Behavior tests for the shared local-server provider lifecycle."""

from __future__ import annotations

import asyncio
import socket
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from marv.llm.local_server import LocalServerProvider

FAKE_SERVER = Path(__file__).parent / "_fake_server.py"


def free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


class _FakeProvider(LocalServerProvider):
    server_label = "fake"
    start_timeout = 20

    def _launch_argv(self, model: str, port: int) -> list[str]:
        return [sys.executable, str(FAKE_SERVER), "--model", model, "--port", str(port)]


class _SingleServerProvider(_FakeProvider):
    restart_on_model_change = False


def make_provider(tmp_path: Path, model: str = "test-model", **kwargs) -> _FakeProvider:
    return _FakeProvider(
        base_url=f"http://127.0.0.1:{free_port()}",
        api_key="",
        model=model,
        log_dir=tmp_path,
        **kwargs,
    )


def test_ensure_running_starts_a_server_then_reports_it_serving(tmp_path: Path):
    provider = make_provider(tmp_path)
    try:
        assert provider.ensure_running() is True
        assert provider.is_serving() is True
        # Already serving: nothing to start.
        assert provider.ensure_running() is False
    finally:
        provider.stop()

    assert provider.is_serving() is False


def test_close_unloads_the_server(tmp_path: Path):
    provider = make_provider(tmp_path)
    assert provider.ensure_running() is True

    asyncio.run(provider.close())

    assert provider.is_serving() is False


def test_a_different_model_is_not_considered_serving(tmp_path: Path):
    provider = make_provider(tmp_path)
    try:
        assert provider.ensure_running() is True
        provider.set_model("another-model")
        assert provider.is_serving() is False
    finally:
        provider.stop()


def test_single_server_provider_serves_any_selected_model(tmp_path: Path):
    provider = _SingleServerProvider(
        base_url=f"http://127.0.0.1:{free_port()}",
        api_key="",
        model="system",
        log_dir=tmp_path,
    )
    try:
        assert provider.ensure_running() is True
        provider.set_model("pcc")
        # One server serves every model, so no restart is needed.
        assert provider.is_serving() is True
        assert provider.ensure_running() is False
    finally:
        provider.stop()


def test_server_pid_prefers_the_process_marv_launched(tmp_path: Path, monkeypatch):
    provider = make_provider(tmp_path)
    provider._server_proc = SimpleNamespace(pid=111, poll=lambda: None)
    monkeypatch.setattr("marv.llm.local_server.listener_pid", lambda base_url: 222)

    assert provider.server_pid == 111


def test_server_pid_falls_back_to_the_port_listener(tmp_path: Path, monkeypatch):
    provider = make_provider(tmp_path)
    monkeypatch.setattr("marv.llm.local_server.listener_pid", lambda base_url: 222)

    assert provider.server_pid == 222


def test_server_pid_ignores_an_exited_process(tmp_path: Path, monkeypatch):
    provider = make_provider(tmp_path)
    provider._server_proc = SimpleNamespace(pid=111, poll=lambda: 1)
    monkeypatch.setattr("marv.llm.local_server.listener_pid", lambda base_url: 222)

    assert provider.server_pid == 222


def test_local_provider_waits_for_prefill_without_a_read_timeout(tmp_path: Path):
    """A local prefill can be minutes of silence before the first token.

    A finite read timeout would kill the stream mid-prefill (and, before the
    fix, retry it), so local servers default to waiting indefinitely.
    """
    provider = make_provider(tmp_path)

    assert provider.read_timeout is None
    assert provider.client.timeout.read is None
    # The connect timeout still guards a wedged server.
    assert provider.client.timeout.connect == 10.0


@pytest.mark.asyncio
async def test_local_provider_read_timeout_is_overridable(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AGENT_LLM_READ_TIMEOUT", "900")
    provider = make_provider(tmp_path)

    assert provider.read_timeout == 900.0
    assert provider.client.timeout.read == 900.0

    await provider.client.aclose()
