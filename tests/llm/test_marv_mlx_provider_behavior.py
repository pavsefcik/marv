"""Behavior tests for the marv-mlx provider."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from marv.llm.events import StreamOptions
from marv.llm.marv_mlx import MarvMlxProvider, resolve_mlx_server_command

if TYPE_CHECKING:
    from pathlib import Path

Qwen = "mlx-community/Qwen3.5-4B-MLX-4bit"
MinistralReasoning = "mlx-community/Ministral-3-8B-Reasoning-2512-4bit"


class _Msg:
    def __init__(self, role: str = "user") -> None:
        self.role = role

    def to_api_dict(self):
        return {"role": self.role, "content": "hi"}


def make_provider(model: str = Qwen, **kwargs) -> MarvMlxProvider:
    return MarvMlxProvider(
        base_url=kwargs.pop("base_url", "http://localhost:11500"),
        api_key="",
        model=model,
        hub_dir=kwargs.pop("hub_dir", None),
        server_command=kwargs.pop("server_command", None),
    )


def test_provider_name_and_no_auth_header():
    provider = make_provider()
    assert provider.name == "marv-mlx"
    assert provider.model == Qwen
    # No API key means no Authorization header is attached.
    assert "Authorization" not in provider.client.headers


def test_thinking_enable_for_qwen_when_level_set():
    provider = make_provider()
    payload = provider._build_payload([_Msg()], None, StreamOptions(thinking_level="medium"))
    assert payload.get("enable_thinking") is True


def test_thinking_disabled_when_off():
    provider = make_provider()
    payload = provider._build_payload([_Msg()], None, StreamOptions(thinking_level="off"))
    assert payload.get("enable_thinking") is False


def test_bracket_markers_for_ministral_reasoning():
    provider = make_provider(MinistralReasoning)
    payload = provider._build_payload([_Msg()], None, None)
    assert payload.get("thinking_start_token") == "[THINK]"
    assert payload.get("thinking_end_token") == "[/THINK]"
    assert payload.get("enable_thinking") is True


def test_supports_thinking_matches_family():
    assert make_provider(Qwen).supports_thinking() is True
    assert make_provider(MinistralReasoning).supports_thinking() is True


def test_launch_argv_runs_mlx_vlm_server_for_the_model():
    provider = make_provider(server_command="mlx_vlm.server")

    assert provider._launch_argv(Qwen, 11500) == [
        "mlx_vlm.server",
        "--host",
        "127.0.0.1",
        "--model",
        Qwen,
        "--port",
        "11500",
    ]


def test_resolve_server_command_accepts_an_explicit_override():
    assert resolve_mlx_server_command("/opt/bin/mlx_vlm.server --trust-remote-code") == [
        "/opt/bin/mlx_vlm.server",
        "--trust-remote-code",
    ]


def test_list_models_scans_the_local_hub(tmp_path: Path, monkeypatch):
    from marv.llm import marv_mlx

    (tmp_path / "models--mlx-community--Qwen3.5-4B-MLX-4bit").mkdir()
    (tmp_path / "models--mlx-community--Gemma-3-4B-it").mkdir()
    monkeypatch.setattr(marv_mlx, "running_model_id", lambda base_url: None)

    provider = make_provider(hub_dir=tmp_path)

    assert asyncio.run(provider.list_models()) == [
        "mlx-community/Gemma-3-4B-it",
        "mlx-community/Qwen3.5-4B-MLX-4bit",
    ]
