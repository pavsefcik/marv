"""Behavior tests for the persisted last-used TUI selection."""

from __future__ import annotations

from marv.config import Config
from marv.config.state import LastUsedSelection, load_last_used, state_path
from marv.runtime.settings import ThinkingLevel


def test_last_used_selection_round_trips_through_disk(temp_dir):
    session_dir = temp_dir / "sessions"

    LastUsedSelection(
        provider="marv-mlx",
        model="mlx-community/Qwen3.5-4B-MLX-4bit",
        thinking_level="high",
    ).save(session_dir)

    loaded = load_last_used(session_dir)

    assert loaded.provider == "marv-mlx"
    assert loaded.model == "mlx-community/Qwen3.5-4B-MLX-4bit"
    assert loaded.thinking_level == "high"


def test_load_last_used_returns_empty_when_state_file_absent(temp_dir):
    loaded = load_last_used(temp_dir / "sessions")

    assert loaded == LastUsedSelection()


def test_config_load_uses_last_used_selection_as_fallback(temp_dir, monkeypatch):
    home = temp_dir / "home"
    project = temp_dir / "project"
    home.mkdir()
    project.mkdir()

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(project)

    LastUsedSelection(
        provider="apple-fm",
        model="system",
        thinking_level="low",
    ).save(home / ".cache" / "marv" / "sessions")

    config = Config.load()

    assert config.provider == "apple-fm"
    assert config.model == "system"
    assert config.thinking_level == ThinkingLevel.LOW


def test_config_load_explicit_model_wins_over_last_used(temp_dir, monkeypatch):
    home = temp_dir / "home"
    project = temp_dir / "project"
    home.mkdir()
    project.mkdir()

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(project)

    LastUsedSelection(provider="marv-mlx", model="remembered-model").save(
        home / ".cache" / "marv" / "sessions"
    )

    global_dir = home / ".marv"
    global_dir.mkdir()
    (global_dir / "config.toml").write_text('model = "configured-model"\n')

    config = Config.load()

    assert config.model == "configured-model"


def test_config_load_env_model_wins_over_last_used(temp_dir, monkeypatch):
    home = temp_dir / "home"
    project = temp_dir / "project"
    home.mkdir()
    project.mkdir()

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(project)
    monkeypatch.setenv("AGENT_MODEL", "env-model")

    LastUsedSelection(model="remembered-model").save(home / ".cache" / "marv" / "sessions")

    config = Config.load()

    assert config.model == "env-model"


def test_state_path_lives_beside_session_directory(temp_dir):
    assert state_path(temp_dir / "sessions") == temp_dir / "state.toml"


# --- per-model latency calibration persistence ------------------------------


def test_latency_fit_round_trips_through_state_toml(temp_dir):
    from marv.llm.latency import LatencyFit, LatencySample

    session_dir = temp_dir / "sessions"
    model = "mlx-community/Qwen3.5-4B-MLX-4bit"
    fit = LatencyFit.from_samples(
        [LatencySample(ttft_ms=2000.0, prompt_tokens=8000, schema_tokens=1000, streamed=True)]
    )

    LastUsedSelection(provider="marv-mlx", model=model).with_fit(model, fit).save(session_dir)

    loaded = load_last_used(session_dir)

    assert loaded.model == model
    restored = loaded.fit_for(model)
    assert restored.prefill_tokens_per_second == fit.prefill_tokens_per_second
    assert restored.sample_count == 1


def test_state_keeps_fits_for_models_other_than_the_selected_one(temp_dir):
    from marv.llm.latency import LatencyFit, LatencySample

    session_dir = temp_dir / "sessions"
    first = LatencyFit.from_samples(
        [LatencySample(ttft_ms=1000.0, prompt_tokens=5000, streamed=True)]
    )
    second = LatencyFit.from_samples(
        [LatencySample(ttft_ms=4000.0, prompt_tokens=10000, streamed=True)]
    )

    state = (
        LastUsedSelection(model="model-b").with_fit("model-a", first).with_fit("model-b", second)
    )
    state.save(session_dir)

    loaded = load_last_used(session_dir)

    assert loaded.fit_for("model-a").prefill_tokens_per_second == 5000.0
    assert loaded.fit_for("model-b").prefill_tokens_per_second == 2500.0


def test_remembering_a_selection_preserves_latency_fits(temp_dir):
    from marv.config.state import remember_latency_fit, remember_selection
    from marv.llm.latency import LatencyFit, LatencySample

    session_dir = temp_dir / "sessions"
    fit = LatencyFit.from_samples(
        [LatencySample(ttft_ms=1000.0, prompt_tokens=5000, streamed=True)]
    )
    remember_latency_fit(session_dir, "model-a", fit)

    # Switching model must not wipe the calibration learned for model-a.
    remember_selection(
        session_dir,
        provider="marv-mlx",
        model="model-b",
        thinking_level="low",
    )

    loaded = load_last_used(session_dir)
    assert loaded.model == "model-b"
    assert loaded.thinking_level == "low"
    assert loaded.fit_for("model-a").sample_count == 1


def test_remembering_a_fit_preserves_selection(temp_dir):
    from marv.config.state import remember_latency_fit
    from marv.llm.latency import LatencyFit, LatencySample

    session_dir = temp_dir / "sessions"
    LastUsedSelection(provider="marv-mlx", model="model-a").save(session_dir)

    remember_latency_fit(
        session_dir,
        "model-a",
        LatencyFit.from_samples([LatencySample(ttft_ms=1000.0, prompt_tokens=5000, streamed=True)]),
    )

    loaded = load_last_used(session_dir)
    assert loaded.provider == "marv-mlx"
    assert loaded.model == "model-a"
    assert loaded.fit_for("model-a").sample_count == 1


def test_fit_for_unknown_model_is_empty(temp_dir):
    state = LastUsedSelection(model="known")

    assert state.fit_for("unknown").has_data is False
    assert state.fit_for(None).has_data is False
