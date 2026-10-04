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
