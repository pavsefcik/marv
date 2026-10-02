"""Behavior tests for the YMLX provider."""

from __future__ import annotations

from marv.llm.events import StreamOptions
from marv.llm.ymlx import YMLXProvider

Qwen = "mlx-community/Qwen3.5-4B-MLX-4bit"
MinistralReasoning = "mlx-community/Ministral-3-8B-Reasoning-2512-4bit"


class _Msg:
    def __init__(self, role: str = "user") -> None:
        self.role = role

    def to_api_dict(self):
        return {"role": self.role, "content": "hi"}


def make_provider(model: str = Qwen, **kwargs) -> YMLXProvider:
    return YMLXProvider(
        base_url=kwargs.pop("base_url", "http://localhost:11500"),
        api_key="",
        model=model,
        hub_dir=kwargs.pop("hub_dir", None),
    )


def test_provider_name_and_no_auth_header():
    provider = make_provider()
    assert provider.name == "ymlx"
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
