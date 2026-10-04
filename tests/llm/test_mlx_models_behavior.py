"""Behavior tests for YMLX model-family classification and hub discovery."""

from __future__ import annotations

from marv.llm.mlx_models import (
    discover_model_ids,
    ministral_base,
    ministral_sibling,
    model_family,
    model_supports_thinking,
    model_thinking_spec,
)


def test_model_family_classification():
    assert model_family("mlx-community/Qwen3.5-4B-MLX-4bit") == "qwen"
    assert model_family("mlx-community/Gemma-3-4B-it") == "gemma"
    assert model_family("mlx-community/Ministral-3-8B-Instruct-2512-4bit") == "ministral-instruct"
    assert model_family("mlx-community/Ministral-3-8B-Reasoning-2512-4bit") == "ministral-reasoning"
    assert model_family("prism-ml/LFM-4B-2bit") == "lfm"
    assert model_family("some-org/Unknown-Model") == "generic"


def test_supports_thinking():
    assert model_supports_thinking("mlx-community/Qwen3.5-4B-MLX-4bit") is True
    assert model_supports_thinking("mlx-community/Gemma-3-4B-it") is True
    assert model_supports_thinking("mlx-community/Ministral-3-8B-Reasoning-2512-4bit") is True
    assert model_supports_thinking("mlx-community/Ministral-3-8B-Instruct-2512-4bit") is False
    assert model_supports_thinking("prism-ml/LFM-4B-2bit") is False


def test_thinking_spec_per_family():
    assert model_thinking_spec("mlx-community/Qwen3.5-4B-MLX-4bit") == (
        "enable_thinking",
        "think",
        False,
    )
    assert model_thinking_spec("mlx-community/Gemma-3-4B-it") == (
        "enable_thinking",
        "channel",
        False,
    )
    assert model_thinking_spec("mlx-community/Ministral-3-8B-Reasoning-2512-4bit") == (
        "variant",
        "bracket",
        True,
    )


def test_ministral_sibling_and_base():
    id_ = "mlx-community/Ministral-3-8B-Instruct-2512-4bit"
    assert ministral_sibling(id_) == "mlx-community/Ministral-3-8B-Reasoning-2512-4bit"
    assert ministral_base(id_) == "Ministral-3-8B-4bit"


def test_discover_collapses_ministral_pair(tmp_path):
    hub = tmp_path / "hub"
    hub.mkdir()
    (hub / "models--mlx-community--Ministral-3-8B-Instruct-2512-4bit").mkdir()
    (hub / "models--mlx-community--Ministral-3-8B-Reasoning-2512-4bit").mkdir()
    (hub / "models--mlx-community--Qwen3.5-4B-MLX-4bit").mkdir()

    ids = discover_model_ids(hub)
    assert ids == [
        "mlx-community/Ministral-3-8B-Instruct-2512-4bit",
        "mlx-community/Qwen3.5-4B-MLX-4bit",
    ]
