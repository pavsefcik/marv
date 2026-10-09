"""Base provider for backends backed by a marv-launched local server process.

Subclasses describe how to launch their server and how to tell whether it is
ready; this class owns the lifecycle: start-on-demand, model switching,
proctitle, readiness polling, and unload-on-exit (including signal/atexit
teardown via ``marv.llm.server_lifecycle``).
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING

from marv.llm.mlx_models import running_model_id
from marv.llm.openai_compat import DEFAULT_LOCAL_READ_TIMEOUT, OpenAICompatibleProvider
from marv.llm.server_lifecycle import register_stop, unregister_stop
from marv.llm.server_process import (
    listener_pid,
    port_of,
    read_tail,
    spawn_detached,
    terminate,
    terminate_proc,
)

if TYPE_CHECKING:
    import subprocess
    from collections.abc import Callable
    from typing import Any

    from marv.llm.events import StreamOptions
    from marv.llm.stream import AssistantMessageEventStream
    from marv.runtime.message import Message


class LocalServerProvider(OpenAICompatibleProvider):
    """OpenAI-compatible provider that marv starts and stops itself."""

    #: Provider name (used by the factory, sessions and capability lookups).
    name: str = "local-server"
    #: Label used in messages and log file names.
    server_label: str = "local-server"
    #: Seconds to wait for the server to become ready.
    start_timeout: float = 20 * 60
    #: Seconds to wait for a graceful stop before escalating to SIGKILL.
    stop_timeout: float = 8.0
    #: Whether selecting a different model needs the server restarted.
    restart_on_model_change: bool = True

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        log_dir: Path | None = None,
        read_timeout: float | None = DEFAULT_LOCAL_READ_TIMEOUT,
    ) -> None:
        super().__init__(
            base_url=base_url,
            api_key=api_key or "",
            model=model,
            name=self.name,
            temperature=temperature,
            max_tokens=max_tokens,
            read_timeout=read_timeout,
        )
        self._server_proc: subprocess.Popen[bytes] | None = None
        self._log_dir = log_dir or Path.home() / ".cache" / "marv" / "logs"
        # Stable bound-method reference so register/unregister can match it.
        self._stop_callback: Callable[[], None] = self.stop_if_serving

    # ---- subclass hooks -------------------------------------------------

    def _launch_argv(self, model: str, port: int) -> list[str]:
        """Command that starts the server for `model` on `port`."""
        raise NotImplementedError

    def _server_is_up(self) -> bool:
        """Whether any server of this kind is answering on the port."""
        return running_model_id(self.base_url) is not None

    def _ready(self) -> bool:
        """Whether the server is ready to serve the selected model."""
        return self.is_serving()

    def _proctitle(self) -> str:
        """Process title for the server (shown in Activity Monitor / `ps`)."""
        return self.model or self.server_label

    # ---- lifecycle ------------------------------------------------------

    def is_serving(self) -> bool:
        """True when the selected model is being served on the port."""
        if not self.model:
            return False
        if not self.restart_on_model_change:
            return self._server_is_up()
        return running_model_id(self.base_url) == self.model

    @property
    def server_pid(self) -> int | None:
        """PID of the model server marv is using, or None when none is running.

        Prefers the process marv launched; falls back to whatever is listening
        on the port so a server started outside marv is still accounted for.
        """
        proc = self._server_proc
        if proc is not None and proc.poll() is None:
            return proc.pid
        return listener_pid(self.base_url)

    def ensure_running(self) -> bool:
        """Ensure the server is up for the selected model.

        Returns True if a server had to be started, False if it was already
        serving.
        """
        if not self.model:
            return False
        if self.is_serving():
            register_stop(self._stop_callback)
            return False
        if self.restart_on_model_change and running_model_id(self.base_url) is not None:
            # A different model occupies the port; unload it ourselves.
            self.stop()
        self._start()
        register_stop(self._stop_callback)
        return True

    async def ensure_running_async(self) -> bool:
        """Ensure the model is serving without blocking the event loop."""
        return await asyncio.to_thread(self.ensure_running)

    def _start(self) -> None:
        port = port_of(self.base_url)
        if port is None:
            raise RuntimeError(f"cannot determine a port from base_url {self.base_url!r}")
        log_path = self._log_dir / f"{self.server_label}-{port}.log"
        argv = self._launch_argv(self.model, port)
        self._server_proc = spawn_detached(argv, self._child_env(), log_path)
        if not self._wait_until_ready():
            detail = read_tail(log_path).strip()
            self.stop()
            raise RuntimeError(
                f"{self.server_label} failed to start {self.model}"
                + (f":\n{detail}" if detail else "")
            )

    def _wait_until_ready(self) -> bool:
        deadline = time.monotonic() + self.start_timeout
        while time.monotonic() < deadline:
            if self._server_proc is not None and self._server_proc.poll() is not None:
                return False
            if self._ready():
                return True
            time.sleep(0.5)
        return False

    def _child_env(self) -> dict[str, str]:
        """Environment for the server, asking it to rename itself to the model."""
        env = dict(os.environ)
        env["MARV_PROCTITLE"] = self._proctitle()
        hook_dir = Path(__file__).resolve().parent / "_proctitle"
        existing = env.get("PYTHONPATH")
        env["PYTHONPATH"] = f"{hook_dir}{os.pathsep}{existing}" if existing else str(hook_dir)
        return env

    def stop(self, *, timeout: float | None = None) -> None:
        """Stop the server marv is using (best-effort).

        Prefers the process marv launched; falls back to whatever is listening
        on the port so a server marv attached to is still unloaded.
        """
        timeout = self.stop_timeout if timeout is None else timeout
        proc = self._server_proc
        if proc is not None:
            self._server_proc = None
            terminate_proc(proc, timeout)
            return
        pid = listener_pid(self.base_url)
        if pid is not None:
            terminate(pid, timeout)

    def stop_if_serving(self) -> None:
        """Stop the server when it is the model this provider is using."""
        if self.model and self.is_serving():
            self.stop()

    def set_model(self, model: str) -> None:
        """Switch the active model (the server is (re)started lazily)."""
        if not model or model == self.model:
            return
        self.model = model
        self._encoder = None

    def stream(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        options: StreamOptions | None = None,
    ) -> AssistantMessageEventStream:
        """Ensure the model is serving, then stream as normal."""
        self.ensure_running()
        return super().stream(messages, tools, options)

    async def close(self) -> None:
        """Unload the server, then close the HTTP client."""
        unregister_stop(self._stop_callback)
        await asyncio.to_thread(self.stop_if_serving)
        await super().close()
