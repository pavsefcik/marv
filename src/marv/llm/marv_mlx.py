"""marv-mlx backend — the local MLX provider.

marv launches `mlx_vlm.server` directly (no external model-manager CLI) and
talks to its OpenAI-compatible endpoint on `localhost:11500`. Compared with the
generic OpenAI-compatible transport this provider adds:

* server lifecycle — starts/stops `mlx_vlm.server` itself, including unload on
  exit or crash;
* model discovery — lists MLX models already downloaded in the local HF hub;
* family-accurate thinking — enables `enable_thinking` and Ministral bracket
  markers per model family, and surfaces the reasoning trace via thinking events.

No API key is required (the endpoint is local and unauthenticated).
"""

from __future__ import annotations

import os
import shlex
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from marv.llm.local_server import LocalServerProvider
from marv.llm.mlx_models import (
    DEFAULT_HUB_DIR,
    discover_model_ids,
    model_supports_thinking,
    model_thinking_spec,
    running_model_id,
)
from marv.llm.model_download import ModelDownload, hub_model_present, is_hub_repo_id

if TYPE_CHECKING:
    from typing import Any

    from marv.llm.events import StreamOptions
    from marv.runtime.message import Message


#: Conservative output cap for the local MLX server. Peak prefill memory
#: scales with prompt + max_tokens, and on unified-memory Macs an oversized
#: budget can push Metal past its working-set limit, surfacing mid-stream as
#: "[METAL] ... Insufficient Memory". ymlx uses the same 2048 default. Override
#: with MARV_MLX_MAX_OUTPUT_TOKENS (<= 0 disables the cap).
DEFAULT_MAX_OUTPUT_TOKENS = 2048


def resolve_max_output_tokens(requested: int) -> int:
    """Clamp the requested output budget to the local server's safe ceiling."""
    cap = DEFAULT_MAX_OUTPUT_TOKENS
    if raw := os.environ.get("MARV_MLX_MAX_OUTPUT_TOKENS"):
        try:
            cap = int(raw)
        except ValueError:
            cap = DEFAULT_MAX_OUTPUT_TOKENS
    if cap <= 0:
        return requested
    return min(requested, cap)


def resolve_mlx_server_command(explicit: str | None = None) -> list[str] | None:
    """Locate the `mlx_vlm.server` entry point, or None when not installed."""
    if explicit:
        return shlex.split(explicit)
    if found := shutil.which("mlx_vlm.server"):
        return [found]
    tool_bin = (
        Path.home() / ".local" / "share" / "uv" / "tools" / "mlx-vlm" / "bin" / "mlx_vlm.server"
    )
    if tool_bin.is_file() and os.access(tool_bin, os.X_OK):
        return [str(tool_bin)]
    if shutil.which("uvx"):
        return ["uvx", "--from", "mlx-vlm", "mlx_vlm.server"]
    return None


class MarvMlxProvider(LocalServerProvider):
    """Provider for a locally-served MLX model."""

    name: str = "marv-mlx"
    server_label = "mlx-vlm"

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        hub_dir: Path | None = None,
        server_command: str | None = None,
    ) -> None:
        super().__init__(
            base_url=base_url,
            api_key=api_key,
            model=model,
            temperature=temperature,
            max_tokens=resolve_max_output_tokens(max_tokens),
        )
        self._hub_dir = hub_dir or DEFAULT_HUB_DIR
        self._server_command = resolve_mlx_server_command(server_command)

    @property
    def hub_dir(self) -> Path:
        return self._hub_dir

    def _launch_argv(self, model: str, port: int) -> list[str]:
        if self._server_command is None:
            raise RuntimeError(
                "mlx-vlm is not installed. Install it with "
                "`uv tool install mlx-vlm --with jinja2 --with setproctitle`."
            )
        return [*self._server_command, "--host", "127.0.0.1", "--model", model, "--port", str(port)]

    async def list_models(self) -> list[str]:
        """List locally downloaded MLX models (hub scan + currently loaded)."""
        models = set(discover_model_ids(self._hub_dir))
        running = running_model_id(self.base_url)
        if running:
            models.add(running)
        return sorted(models)

    def is_model_downloaded(self, model: str) -> bool:
        """Whether ``model`` can be run without downloading it first.

        Non-hub model ids (bare names, local paths) report as present so the
        auto-download path never fetches something that is not a hub repo. A
        model the server is already serving also counts, so a hub-cache mismatch
        cannot trigger a needless re-download.
        """
        if not is_hub_repo_id(model):
            return True
        if hub_model_present(self._hub_dir, model):
            return True
        return running_model_id(self.base_url) == model

    def download_model(self, model: str) -> ModelDownload:
        """Prepare a download of ``model`` into the local HF hub.

        The download does not start until :meth:`ModelDownload.start` (or
        ``run``) is called, so the caller can show progress first.
        """
        return ModelDownload(model_id=model, hub_dir=self._hub_dir)

    def supports_thinking(self) -> bool:
        """Thinking support is per model family."""
        return model_supports_thinking(self.model, self._hub_dir)

    def _build_payload(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None,
        options: StreamOptions | None,
    ) -> dict[str, Any]:
        payload = super()._build_payload(messages, tools, options)

        # The agent forwards its configured output budget via StreamOptions;
        # clamp whatever wins so a large config cannot ask the local server for
        # more headroom than the GPU can wire down at once.
        if "max_tokens" in payload:
            payload["max_tokens"] = resolve_max_output_tokens(int(payload["max_tokens"]))

        # Remove any marker set by the base (we decide it below per family).
        payload.pop("enable_thinking", None)
        payload.pop("thinking_start_token", None)
        payload.pop("thinking_end_token", None)

        control, markers, _reasoning_first = model_thinking_spec(self.model, self._hub_dir)

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
