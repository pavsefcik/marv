"""Behavior tests for local-server process helpers."""

from __future__ import annotations

import socket
import subprocess
import sys
import time

import pytest

from marv.llm.server_process import (
    listener_pid,
    port_of,
    process_alive,
    server_reachable,
    terminate,
)


def free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def test_port_of_parses_the_url():
    assert port_of("http://127.0.0.1:11500") == 11500
    assert port_of("http://localhost") is None


def test_server_reachable_is_false_when_nothing_is_listening():
    assert server_reachable(f"http://127.0.0.1:{free_port()}") is False


def test_listener_pid_finds_and_terminate_kills_the_listener():
    port = free_port()
    child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "import socket, time; "
                "s = socket.socket(); "
                "s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); "
                f"s.bind(('127.0.0.1', {port})); s.listen(1); time.sleep(30)"
            ),
        ],
    )
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if listener_pid(f"http://127.0.0.1:{port}") == child.pid:
                break
            time.sleep(0.1)
        else:
            pytest.fail("dummy listener never came up")

        terminate(child.pid, timeout=5)

        assert process_alive(child.pid) is False
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()
