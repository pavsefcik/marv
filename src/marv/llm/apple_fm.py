"""apple-fm backend — Apple's built-in Foundation Model (macOS 27+).

`fm serve` exposes the on-device Apple Foundation Model (and, on supported
hardware, Private Cloud Compute) through an OpenAI-compatible endpoint. marv
launches that server itself and unloads it on exit.

Unlike the MLX backend, one server serves every model, so switching between
`system` and `pcc` does not restart anything. The endpoint does not support
OpenAI function/tool calling, so this backend is chat-only: marv hides its tool
suite and answers in plain text.
"""

from __future__ import annotations

import shutil

from marv.llm.local_server import LocalServerProvider
from marv.llm.server_process import list_served_models, server_reachable

#: Models the CLI advertises even before the server is running.
BUILTIN_MODELS = ["system", "pcc"]

#: `fm serve`'s default example port.
DEFAULT_FM_PORT = 1976


def resolve_fm_command(explicit: str | None = None) -> str | None:
    """Locate the `fm` CLI, or None when it is unavailable."""
    if explicit:
        return explicit
    if found := shutil.which("fm"):
        return found
    fallback = "/usr/bin/fm"
    return fallback if shutil.which(fallback) else None


class AppleFMProvider(LocalServerProvider):
    """Provider for Apple's on-device Foundation Model via `fm serve`."""

    name: str = "apple-fm"
    server_label = "apple-fm"
    #: One server serves every model, so a model change needs no restart.
    restart_on_model_change = False
    #: `fm serve` has no OpenAI function-calling support.
    supports_tools = False

    def __init__(
        self,
        *,
        base_url: str = f"http://127.0.0.1:{DEFAULT_FM_PORT}",
        api_key: str = "",
        model: str = "system",
        temperature: float = 0.7,
        max_tokens: int = 4096,
        fm_command: str | None = None,
    ) -> None:
        super().__init__(
            base_url=base_url,
            api_key=api_key,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        self._fm_command = resolve_fm_command(fm_command)

    def _launch_argv(self, model: str, port: int) -> list[str]:
        if self._fm_command is None:
            raise RuntimeError(
                "the Foundation Models CLI ('fm') is not available. "
                "It requires macOS 27+; run `fm license` (with sudo) once to enable it."
            )
        return [self._fm_command, "serve", "--port", str(port)]

    def _server_is_up(self) -> bool:
        return server_reachable(self.base_url)

    async def list_models(self) -> list[str]:
        """List models the server advertises, falling back to the built-ins."""
        return list_served_models(self.base_url) or list(BUILTIN_MODELS)

    def supports_thinking(self) -> bool:
        """Apple's Foundation Model does not expose a reasoning trace."""
        return False
