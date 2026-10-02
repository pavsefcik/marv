"""YMLX-backed LLM provider.

YMLX (`pavsefcik/ymlx`) manages locally downloaded MLX models and serves the
active one through an OpenAI-compatible endpoint on `localhost:11500`. This
provider extends the generic OpenAI-compatible transport with:

* server lifecycle — ensures a model is running on the ymlx port by shelling out
  to the `ymlx` CLI (`ymlx run <id>` / `ymlx stop`);
* model discovery — lists ymlx-managed models from the local HF hub directory
  (the picker shows what ymlx manages, not just the currently loaded model);
* family-accurate thinking — enables `enable_thinking` and Ministeral bracket
  markers per model family, and surfaces the reasoning trace via thinking events.

No API key is required (YMLX is a local, unauthenticated endpoint).
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
from typing import TYPE_CHECKING

from marv.llm.openai_compat import OpenAICompatibleProvider
from marv.llm.ymlx_models import (
    DEFAULT_HUB_DIR,
    discover_yaml_model_ids,
    running_model_id,
    ymlx_supports_thinking,
    ymlx_thinking_spec,
)

if TYPE_CHECKING:
    from pathlib import Path
    from typing import Any

    from marv.llm.events import StreamOptions
    from marv.llm.stream import AssistantMessageEventStream
    from marv.runtime.message import Message


class YMLXProvider(OpenAICompatibleProvider):
    """Provider for a YMLX-served MLX model."""

    name: str = "ymlx"

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        hub_dir: Path | None = None,
        ymlx_command: str = "ymlx",
    ) -> None:
        super().__init__(
            base_url=base_url,
            api_key=api_key or "",
            model=model,
            name="ymlx",
            temperature=temperature,
            max_tokens=max_tokens,
        )
        self._hub_dir = hub_dir or DEFAULT_HUB_DIR
        self._ymlx_command = ymlx_command

    @property
    def hub_dir(self) -> Path:
        return self._hub_dir

    def _resolve_ymlx_invocation(self) -> list[str] | None:
        """Return argv head for the ymlx CLI, or None if unavailable."""
        if self._ymlx_command and shutil.which(self._ymlx_command):
            return [self._ymlx_command]
        if shutil.which("zsh"):
            return ["zsh", "-ic", "ymlx"]
        return None

    def _run_cli(self, action: str, *args: str) -> subprocess.CompletedProcess[str] | None:
        """Run a ymlx subcommand, capturing its output (no terminal clobber).

        Returns None when the ymlx CLI is unavailable.
        """
        argv = self._resolve_ymlx_invocation()
        if argv is None:
            return None
        cmd = [*argv, action, *args]
        try:
            return subprocess.run(
                cmd,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"timed out running `ymlx {action}` for {self.model}") from exc

    def is_serving(self) -> bool:
        """True when the configured model is already the one serving on the port."""
        return bool(self.model) and running_model_id(self.base_url) == self.model

    def ensure_running(self) -> bool:
        """Ensure the configured model is serving on the ymlx port.

        Returns True if the server was (re)started for this model, False if it
        was already serving or the ymlx CLI is unavailable (nothing to do and
        no feedback needed). If the model is different to what's running,
        `ymlx run <id>` stops it and starts the requested one (blocking until
        ready). Populated output is captured so it never writes to the
        controlling terminal.
        """
        if not self.model:
            return False
        if running_model_id(self.base_url) == self.model:
            return False
        proc = self._run_cli("run", self.model)
        if proc is None:
            return False
        if proc.returncode != 0 or running_model_id(self.base_url) != self.model:
            detail = (proc.stdout or "").strip() or f"failed to start {self.model}"
            raise RuntimeError(detail)
        return True

    async def ensure_running_async(self) -> bool:
        """Ensure the model is serving without blocking the event loop.

        Returns True if the model had to be (re)started.
        """
        started = await asyncio.to_thread(self.ensure_running)
        return started

    def stop(self) -> None:
        """Stop the ymlx server (best-effort)."""
        self._run_cli("stop")

    async def list_models(self) -> list[str]:
        """List ymlx-managed models (hub scan + currently running model)."""
        models = set(discover_yaml_model_ids(self._hub_dir))
        running = running_model_id(self.base_url)
        if running:
            models.add(running)
        return sorted(models)

    def supports_thinking(self) -> bool:
        """Thinking support is per ymlx model family."""
        return ymlx_supports_thinking(self.model, self._hub_dir)

    def set_model(self, model: str) -> None:
        """Switch to another ymlx-model and arrange for it to start serving."""
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

    def _build_payload(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None,
        options: StreamOptions | None,
    ) -> dict[str, Any]:
        payload = super()._build_payload(messages, tools, options)

        # Remove any marker set by the base (we decide it below per family).
        payload.pop("enable_thinking", None)
        payload.pop("thinking_start_token", None)
        payload.pop("thinking_end_token", None)

        spec = ymlx_thinking_spec(self.model, self._hub_dir)
        control, markers, reasoning_first = spec

        if control == "enable_thinking":
            enabled = bool(options and options.thinking_level and options.thinking_level != "off")
            payload["enable_thinking"] = enabled
        elif control == "variant" and markers == "bracket":
            # Ministral Reasoning: cannot toggle generation; tell the server to
            # split its inherent trace into reasoning_content via bracket markers.
            payload["thinking_start_token"] = "[THINK]"
            payload["thinking_end_token"] = "[/THINK]"
            payload["enable_thinking"] = True

        return payload

    async def close(self) -> None:
        await super().close()
