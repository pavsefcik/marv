"""Behavior tests for YMLX model-family classification and hub discovery."""

from __future__ import annotations

from marv.llm.mlx_models import (
    discover_model_ids,
    ministral_base,
    ministral_sibling,
    model_family,
    model_supports_thinking,
    model_supports_vision,
    model_thinking_spec,
)

TEXT_MODEL = "mlx-community/Llama-3.2-3B-Instruct-4bit"
VLM_MODEL = "mlx-community/Qwen2-VL-2B-Instruct-4bit"


def write_snapshot(hub, model_id: str, files: dict[str, str]):
    """Create one local HF snapshot for `model_id` holding `files`."""
    snapshot = hub / f"models--{model_id.replace('/', '--')}" / "snapshots" / "rev"
    snapshot.mkdir(parents=True)
    for name, text in files.items():
        (snapshot / name).write_text(text)
    return snapshot


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


def test_model_supports_vision_reads_an_image_processor_config(tmp_path):
    for name in (
        "preprocessor_config.json",
        "processor_config.json",
        "image_processor_config.json",
    ):
        hub = tmp_path / name
        write_snapshot(hub, TEXT_MODEL, {name: "{}"})

        assert model_supports_vision(TEXT_MODEL, hub) is True, name


def test_model_supports_vision_reads_vision_markers_in_config(tmp_path):
    for label, marker in (
        ("vision_config", '"vision_config": {}'),
        ("vision_tower", '"vision_tower": "siglip"'),
        ("image_token_index", '"image_token_index": 151655'),
        ("vision_feature_layer", '"vision_feature_layer": -2'),
    ):
        hub = tmp_path / label
        write_snapshot(hub, TEXT_MODEL, {"config.json": f'{{"model_type": "qwen2", {marker}}}'})

        assert model_supports_vision(TEXT_MODEL, hub) is True, label


def test_model_supports_vision_rejects_a_plain_text_config(tmp_path):
    hub = tmp_path / "hub"
    write_snapshot(hub, TEXT_MODEL, {"config.json": '{"model_type": "llama", "hidden_size": 3072}'})
    write_snapshot(hub, "mlx-community/LFM-4B-2bit", {"config.json": '{"model_type": "lfm2"}'})

    assert model_supports_vision(TEXT_MODEL, hub) is False
    assert model_supports_vision("mlx-community/LFM-4B-2bit", hub) is False


def test_model_supports_vision_is_permissive_without_a_snapshot(tmp_path):
    hub = tmp_path / "hub"
    hub.mkdir()

    assert model_supports_vision(TEXT_MODEL, hub) is True
    assert model_supports_vision(VLM_MODEL, hub) is True


def test_model_supports_vision_trusts_a_vlm_name(tmp_path):
    hub = tmp_path / "hub"
    for model_id in (
        VLM_MODEL,
        "mlx-community/llava-1.5-7B-4bit",
        "mlx-community/pixtral-12b-4bit",
        "mlx-community/SmolVLM-Instruct-4bit",
    ):
        write_snapshot(
            hub, model_id, {"config.json": '{"model_type": "unknown-to-the-name-heuristic"}'}
        )

        assert model_supports_vision(model_id, hub) is True, model_id


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
