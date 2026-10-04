"""Process helpers for marv-managed local model servers."""

from __future__ import annotations

import contextlib
import json
import os
import signal
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit


def port_of(base_url: str) -> int | None:
    """Return the TCP port in `base_url`, or None if it has none."""
    try:
        return urlsplit(base_url).port
    except ValueError:
        return None


def process_alive(pid: int) -> bool:
    """True while `pid` is a live process.

    A zombie still accepts signal 0, so its state is checked explicitly; this
    matters when the process was not started by marv and cannot be reaped here.
    """
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:
        result = subprocess.run(
            ["ps", "-o", "stat=", "-p", str(pid)],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return True
    return not result.stdout.strip().startswith("Z")


def listener_pid(base_url: str) -> int | None:
    """PID listening on the port in `base_url`, or None if nothing is."""
    port = port_of(base_url)
    if port is None:
        return None
    try:
        result = subprocess.run(
            ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    for token in result.stdout.split():
        if token.isdigit():
            return int(token)
    return None


def _fetch_models(base_url: str, *, timeout: float) -> list[dict[str, object]] | None:
    url = base_url.rstrip("/") + "/v1/models"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            data = json.load(response)
    except (urllib.error.URLError, OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("data"), list):
        return None
    return [entry for entry in data["data"] if isinstance(entry, dict)]


def server_reachable(base_url: str, *, timeout: float = 2.0) -> bool:
    """True when the OpenAI-compatible endpoint answers ``GET /v1/models``."""
    return _fetch_models(base_url, timeout=timeout) is not None


def list_served_models(base_url: str, *, timeout: float = 2.0) -> list[str]:
    """Model ids advertised by a running server, or [] when it is not up."""
    entries = _fetch_models(base_url, timeout=timeout)
    if entries is None:
        return []
    return [str(entry["id"]) for entry in entries if isinstance(entry.get("id"), str)]


def terminate(pid: int, timeout: float) -> None:
    """SIGTERM a process marv did not spawn, escalating to SIGKILL after `timeout`."""
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not process_alive(pid):
            return
        time.sleep(0.2)
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.kill(pid, signal.SIGKILL)


def terminate_proc(proc: subprocess.Popen[bytes], timeout: float) -> None:
    """Stop a process marv spawned, reaping it so no zombie is left behind."""
    with contextlib.suppress(ProcessLookupError, OSError):
        proc.terminate()
    try:
        proc.wait(timeout=timeout)
        return
    except subprocess.TimeoutExpired:
        pass
    with contextlib.suppress(ProcessLookupError, OSError):
        proc.kill()
    with contextlib.suppress(subprocess.TimeoutExpired):
        proc.wait(timeout=5)


def spawn_detached(argv: list[str], env: dict[str, str], log_path: Path) -> subprocess.Popen[bytes]:
    """Start `argv` detached, appending its output to `log_path`.

    The child gets its own session so it survives marv's terminal but can still
    be stopped by PID. Returns the running process handle.
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("ab") as log:
        return subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            env=env,
            cwd=str(Path.home()),
        )


def read_tail(log_path: Path, lines: int = 20) -> str:
    """Return the last `lines` lines of `log_path` (empty if unreadable)."""
    try:
        content = log_path.read_text(errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(content[-lines:])
