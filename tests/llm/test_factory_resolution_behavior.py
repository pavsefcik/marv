"""Behavior tests for LLM provider factory resolution."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from marv.llm.factory import create_provider, resolve_provider_config

if TYPE_CHECKING:
    from marv.config import Config


def resolve_from_config(config: Config):
    return resolve_provider_config(
        provider=config.provider,
        model=config.model,
        api_key=config.api_key,
        base_url=config.base_url,
        provider_overrides=config.provider_overrides(),
    )


def test_marv_mlx_provider_uses_local_default_base_url_and_no_api_key():
    provider_config = resolve_provider_config(
        provider="marv-mlx",
        model="mlx-community/Qwen3.5-4B-MLX-4bit",
        api_key=None,
        base_url=None,
        provider_overrides=None,
    )

    assert provider_config.base_url == "http://localhost:11500"
    assert provider_config.model == "mlx-community/Qwen3.5-4B-MLX-4bit"
    assert provider_config.api_key == ""


def test_marv_mlx_provider_uses_provider_override_model_when_unset():
    override = SimpleNamespace(
        base_url="http://localhost:11900",
        model="mlx-community/Qwen3.6-9B-MLX-4Bit",
        api_key=None,
    )

    provider_config = resolve_provider_config(
        provider="marv-mlx",
        model=None,
        api_key=None,
        base_url=None,
        provider_overrides={"marv-mlx": override},
    )

    assert provider_config.base_url == "http://localhost:11900"
    assert provider_config.model == "mlx-community/Qwen3.6-9B-MLX-4Bit"


def test_resolve_provider_config_prefers_explicit_model_over_provider_override():
    override = SimpleNamespace(
        base_url="http://localhost:11900",
        model="override-model",
        api_key=None,
    )

    provider_config = resolve_provider_config(
        provider="marv-mlx",
        model="mlx-community/Qwen3.5-4B-MLX-4bit",
        api_key=None,
        base_url=None,
        provider_overrides={"marv-mlx": override},
    )

    assert provider_config.model == "mlx-community/Qwen3.5-4B-MLX-4bit"


def test_resolve_provider_config_prefers_explicit_base_url_over_provider_override():
    override = SimpleNamespace(
        base_url="https://override.example",
        model="mlx-community/Qwen3.5-4B-MLX-4bit",
        api_key=None,
    )

    provider_config = resolve_provider_config(
        provider="marv-mlx",
        model="mlx-community/Qwen3.5-4B-MLX-4bit",
        api_key=None,
        base_url="https://explicit.example",
        provider_overrides={"marv-mlx": override},
    )

    assert provider_config.base_url == "https://explicit.example"


def test_create_provider_builds_marv_mlx_provider_instance():
    provider = create_provider(
        provider="marv-mlx",
        model="mlx-community/Qwen3.5-4B-MLX-4bit",
        api_key=None,
        base_url=None,
        temperature=0.7,
        max_output_tokens=4096,
        provider_overrides=None,
    )

    assert provider.name == "marv-mlx"
    assert provider.model == "mlx-community/Qwen3.5-4B-MLX-4bit"


def test_create_provider_builds_openai_compatible_provider_with_override():
    override = SimpleNamespace(
        base_url="https://some-server.example",
        model="some-openai-compatible-model",
        api_key="sk-test",
    )
    provider = create_provider(
        provider="custom",
        model="some-openai-compatible-model",
        api_key=None,
        base_url=None,
        temperature=0.7,
        max_output_tokens=4096,
        provider_overrides={"custom": override},
    )

    assert provider.name == "custom"
    assert provider.model == "some-openai-compatible-model"


def test_apple_fm_provider_uses_local_defaults_and_no_api_key():
    provider_config = resolve_provider_config(
        provider="apple-fm",
        model=None,
        api_key=None,
        base_url=None,
        provider_overrides=None,
    )

    assert provider_config.base_url == "http://127.0.0.1:1976"
    assert provider_config.model == "system"
    assert provider_config.api_key == ""


def test_create_provider_builds_apple_fm_provider_instance():
    provider = create_provider(
        provider="apple-fm",
        model=None,
        api_key=None,
        base_url=None,
        temperature=0.7,
        max_output_tokens=4096,
        provider_overrides=None,
    )

    assert provider.name == "apple-fm"
    assert provider.model == "system"
    assert provider.supports_tools is False


def test_create_provider_requires_model_for_compatible_provider_when_unset():
    with pytest.raises(ValueError, match="Model is required for provider 'custom'"):
        create_provider(
            provider="custom",
            model=None,
            api_key="sk-test",
            base_url=None,
            temperature=0.7,
            max_output_tokens=4096,
            provider_overrides=None,
        )
