"""Behavior tests for CLI run command flows via CLI runner boundary."""

from __future__ import annotations

import textwrap
from typing import TYPE_CHECKING

from typer.testing import CliRunner

from marv import cli
from marv.runtime.session import Session

if TYPE_CHECKING:
    from pathlib import Path


def write_extension(path: Path) -> None:
    path.write_text(
        textwrap.dedent(
            """
            from marv.extensions.api import ExtensionAPI

            def setup(api: ExtensionAPI):
                def dump(args, ctx):
                    cfg = ctx.get_config()
                    return "|".join([
                        str(cfg["prompt_template_dirs"]),
                        str(cfg["context_file_paths"]),
                        str(cfg["custom_system_prompt"]),
                        str(cfg["append_system_prompt"]),
                    ])
                api.register_command("dump-config", dump)
            """
        )
    )


def test_cli_preserves_prompt_and_context_fields(temp_dir, monkeypatch):
    project = temp_dir / "project"
    project.mkdir()
    monkeypatch.chdir(project)

    agent_dir = project / ".marv"
    agent_dir.mkdir()

    prompts_dir = project / "prompts"
    prompts_dir.mkdir()

    context_file = project / "CONTEXT.md"
    context_file.write_text("context")

    (agent_dir / "config.toml").write_text(
        textwrap.dedent(
            f"""
            custom_system_prompt = "custom"
            append_system_prompt = "append"
            prompt_template_dirs = ["{prompts_dir}"]
            context_file_paths = ["{context_file}"]
            """
        )
    )

    ext_path = project / "ext_dump.py"
    write_extension(ext_path)

    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        [
            "run",
            "--headless",
            "--extension",
            str(ext_path),
            "/dump-config",
            "-m",
            "gpt-5.4",
        ],
    )

    assert result.exit_code == 0
    output = result.stdout
    assert "custom" in output
    assert "append" in output
    assert str(prompts_dir) in output
    assert str(context_file) in output


def test_cli_run_rejects_invalid_thinking_level():
    runner = CliRunner()
    result = runner.invoke(cli.app, ["run", "--headless", "hi", "--thinking", "invalid"])

    assert result.exit_code == 1
    assert "Invalid thinking level" in result.stderr


def test_cli_run_rejects_invalid_approval_mode():
    runner = CliRunner()
    result = runner.invoke(cli.app, ["run", "--headless", "hi", "--approval", "bogus"])

    assert result.exit_code == 1
    assert "Invalid approval mode" in result.stderr


def test_cli_run_propagates_approval_mode_to_config(temp_dir, monkeypatch):
    from marv.runtime.approval import ApprovalMode

    project = temp_dir / "project"
    project.mkdir()
    monkeypatch.chdir(project)

    seen: list[ApprovalMode] = []
    sentinel_provider = object()

    def fake_create_provider(config):
        return sentinel_provider

    async def fake_run_headless(config, prompt, session, llm_provider):
        seen.append(config.approval_mode)

    monkeypatch.setattr(cli, "_create_llm_provider", fake_create_provider)
    monkeypatch.setattr(cli, "_run_headless", fake_run_headless)

    runner = CliRunner()
    result = runner.invoke(cli.app, ["run", "--headless", "hi", "--approval", "destructive"])

    assert result.exit_code == 0
    assert seen == [ApprovalMode.DESTRUCTIVE]


def test_cli_run_propagates_theme_to_config(temp_dir, monkeypatch):
    project = temp_dir / "project"
    project.mkdir()
    monkeypatch.chdir(project)

    seen: list[str] = []
    sentinel_provider = object()

    def fake_create_provider(config):
        return sentinel_provider

    async def fake_run_headless(config, prompt, session, llm_provider):
        seen.append(config.theme)

    monkeypatch.setattr(cli, "_create_llm_provider", fake_create_provider)
    monkeypatch.setattr(cli, "_run_headless", fake_run_headless)

    runner = CliRunner()
    result = runner.invoke(cli.app, ["run", "--headless", "hi", "--theme", "nord"])

    assert result.exit_code == 0
    assert seen == ["nord"]


def test_cli_run_keeps_the_configured_theme_without_the_flag(temp_dir, monkeypatch):
    project = temp_dir / "project"
    project.mkdir()
    monkeypatch.chdir(project)

    seen: list[str] = []

    def fake_create_provider(config):
        return object()

    async def fake_run_headless(config, prompt, session, llm_provider):
        seen.append(config.theme)

    monkeypatch.setattr(cli, "_create_llm_provider", fake_create_provider)
    monkeypatch.setattr(cli, "_run_headless", fake_run_headless)
    monkeypatch.setenv("AGENT_THEME", "dracula")

    runner = CliRunner()
    result = runner.invoke(cli.app, ["run", "--headless", "hi"])

    assert result.exit_code == 0
    assert seen == ["dracula"]


def test_cli_run_headless_requires_prompt():
    runner = CliRunner()
    result = runner.invoke(cli.app, ["run", "--headless"])

    assert result.exit_code == 1
    assert "Prompt required in headless mode" in result.stderr


def test_cli_run_fails_on_invalid_provider_model_pair():
    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        ["run", "--headless", "hi", "--provider", "openai", "--model", "claude-sonnet-4-5"],
    )

    assert result.exit_code != 0
    assert isinstance(result.exception, ValueError)
    assert "not valid for provider" in str(result.exception)


def test_cli_run_headless_loads_explicit_session(temp_dir, monkeypatch):
    project = temp_dir / "project"
    project.mkdir()
    monkeypatch.chdir(project)

    ext_path = project / "ext_dump.py"
    write_extension(ext_path)
    session = Session.new(temp_dir)

    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        [
            "run",
            "--headless",
            "--session",
            str(session.path),
            "-m",
            "mlx-community/Qwen3.5-4B-MLX-4bit",
            "--extension",
            str(ext_path),
            "/dump-config",
        ],
    )

    assert result.exit_code == 0
    assert "Loaded session:" in result.stdout


def test_cli_run_errors_when_explicit_session_path_missing(temp_dir, monkeypatch):
    project = temp_dir / "project"
    project.mkdir()
    monkeypatch.chdir(project)

    missing = temp_dir / "missing.jsonl"

    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        [
            "run",
            "--headless",
            "hello",
            "-m",
            "mlx-community/Qwen3.5-4B-MLX-4bit",
            "--session",
            str(missing),
        ],
    )

    assert result.exit_code == 1
    assert "Session file not found" in result.stderr


def test_cli_run_resume_without_previous_session_still_runs_headless(temp_dir, monkeypatch):
    home = temp_dir / "home"
    project = temp_dir / "project"
    home.mkdir()
    project.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(project)

    ext_path = project / "ext_dump.py"
    write_extension(ext_path)

    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        [
            "run",
            "--resume",
            "--headless",
            "-m",
            "mlx-community/Qwen3.5-4B-MLX-4bit",
            "--extension",
            str(ext_path),
            "/dump-config",
        ],
    )

    assert result.exit_code == 0
    assert "No previous session found" in result.stderr


def test_cli_run_dispatches_to_tui_when_not_headless(temp_dir, monkeypatch):
    project = temp_dir / "project"
    project.mkdir()
    monkeypatch.chdir(project)

    calls: dict[str, int] = {"tui": 0, "headless": 0}
    sentinel_provider = object()

    def fake_create_provider(config):
        return sentinel_provider

    def fake_run_tui(config, session, llm_provider):
        assert session is None
        assert llm_provider is sentinel_provider
        calls["tui"] += 1

    async def fake_run_headless(config, prompt, session, llm_provider):
        calls["headless"] += 1

    monkeypatch.setattr(cli, "_create_llm_provider", fake_create_provider)
    monkeypatch.setattr(cli, "_run_tui", fake_run_tui)
    monkeypatch.setattr(cli, "_run_headless", fake_run_headless)

    runner = CliRunner()
    result = runner.invoke(cli.app, ["run"])

    assert result.exit_code == 0
    assert calls["tui"] == 1
    assert calls["headless"] == 0


def test_bare_cli_dispatches_to_tui_when_no_subcommand(temp_dir, monkeypatch):
    project = temp_dir / "project"
    project.mkdir()
    monkeypatch.chdir(project)

    calls: dict[str, int] = {"tui": 0, "headless": 0}
    sentinel_provider = object()

    def fake_create_provider(config):
        return sentinel_provider

    def fake_run_tui(config, session, llm_provider):
        assert session is None
        assert llm_provider is sentinel_provider
        calls["tui"] += 1

    async def fake_run_headless(config, prompt, session, llm_provider):
        calls["headless"] += 1

    monkeypatch.setattr(cli, "_create_llm_provider", fake_create_provider)
    monkeypatch.setattr(cli, "_run_tui", fake_run_tui)
    monkeypatch.setattr(cli, "_run_headless", fake_run_headless)

    runner = CliRunner()
    result = runner.invoke(cli.app, [])

    assert result.exit_code == 0
    assert calls["tui"] == 1
    assert calls["headless"] == 0


def test_cli_run_dispatches_to_headless_when_prompt_or_headless_flag(temp_dir, monkeypatch):
    project = temp_dir / "project"
    project.mkdir()
    monkeypatch.chdir(project)

    calls: dict[str, int] = {"tui": 0, "headless": 0}
    seen_prompt: list[str] = []
    sentinel_provider = object()

    def fake_create_provider(config):
        return sentinel_provider

    def fake_run_tui(config, session, llm_provider):
        calls["tui"] += 1

    async def fake_run_headless(config, prompt, session, llm_provider):
        assert session is None
        assert llm_provider is sentinel_provider
        seen_prompt.append(prompt)
        calls["headless"] += 1

    monkeypatch.setattr(cli, "_create_llm_provider", fake_create_provider)
    monkeypatch.setattr(cli, "_run_tui", fake_run_tui)
    monkeypatch.setattr(cli, "_run_headless", fake_run_headless)

    runner = CliRunner()
    result = runner.invoke(cli.app, ["run", "--headless", "hello"])

    assert result.exit_code == 0
    assert calls["tui"] == 0
    assert calls["headless"] == 1
    assert seen_prompt == ["hello"]


def test_cli_version_flag_prints_package_version():
    from marv import __version__

    runner = CliRunner()
    result = runner.invoke(cli.app, ["--version"])

    assert result.exit_code == 0
    assert result.stdout.strip() == f"marv {__version__}"


class HeadlessHubProvider:
    """Provider double that reports a model as missing and records downloads."""

    def __init__(
        self,
        *,
        model: str = "mlx-community/Qwen3.5-4B-MLX-4bit",
        downloaded: bool = False,
        fail: bool = False,
    ) -> None:
        self.name = "marv-mlx"
        self.model = model
        self._downloaded = downloaded
        self._fail = fail
        self.started = 0
        self.polls = 0

    def is_model_downloaded(self, model: str) -> bool:
        return self._downloaded

    def download_model(self, model: str) -> object:
        outer = self

        class _Download:
            model_id = model
            state = "running"
            progress = _progress(512 * 1024**2, 4 * 1024**3)
            error = "gated repo" if outer._fail else None
            local_path = None

            @property
            def finished(self) -> bool:
                return outer.polls >= 2

            def start(self) -> None:
                outer.started += 1

            def poll(self) -> list[object]:
                outer.polls += 1
                if self.finished:
                    self.state = "failed" if outer._fail else "done"
                return []

            def wait(self, timeout: float | None = None) -> None: ...

            def cancel(self) -> None: ...

        return _Download()


def _progress(downloaded: int, total: int):
    from marv.llm.model_download import DownloadProgress

    return DownloadProgress(downloaded_bytes=downloaded, total_bytes=total)


def run_headless_with_provider(
    monkeypatch, provider: HeadlessHubProvider, *extra_args: str
) -> tuple[int, str, str]:
    seen: list[str] = []

    def fake_create_provider(config):
        return provider

    async def fake_run_headless(config, prompt, session, llm_provider):
        seen.append(prompt)

    monkeypatch.setattr(cli, "_create_llm_provider", fake_create_provider)
    monkeypatch.setattr(cli, "_run_headless", fake_run_headless)

    runner = CliRunner()
    result = runner.invoke(cli.app, ["run", "--headless", "hi", *extra_args])
    return result.exit_code, result.stdout, result.stderr


def test_cli_headless_downloads_a_missing_model_with_visible_progress(temp_dir, monkeypatch):
    monkeypatch.chdir(temp_dir)
    provider = HeadlessHubProvider(downloaded=False)

    exit_code, _stdout, stderr = run_headless_with_provider(monkeypatch, provider)

    assert exit_code == 0
    assert provider.started == 1
    assert "Downloading mlx-community/Qwen3.5-4B-MLX-4bit" in stderr
    assert "512M / 4.0G (12%)" in stderr


def test_cli_headless_skips_download_when_the_model_is_present(temp_dir, monkeypatch):
    monkeypatch.chdir(temp_dir)
    provider = HeadlessHubProvider(downloaded=True)

    exit_code, _stdout, stderr = run_headless_with_provider(monkeypatch, provider)

    assert exit_code == 0
    assert provider.started == 0
    assert "Downloading" not in stderr


def test_cli_headless_fails_when_the_model_download_fails(temp_dir, monkeypatch):
    monkeypatch.chdir(temp_dir)
    provider = HeadlessHubProvider(downloaded=False, fail=True)

    exit_code, _stdout, stderr = run_headless_with_provider(monkeypatch, provider)

    assert exit_code == 1
    assert "Download failed: gated repo" in stderr


def test_cli_does_not_touch_providers_without_a_hub(temp_dir, monkeypatch):
    monkeypatch.chdir(temp_dir)

    class CloudProvider:
        name = "openai"
        model = "gpt-5.4"

    async def fake_run_headless(config, prompt, session, llm_provider):
        return None

    monkeypatch.setattr(cli, "_create_llm_provider", lambda config: CloudProvider())
    monkeypatch.setattr(cli, "_run_headless", fake_run_headless)

    runner = CliRunner()
    result = runner.invoke(cli.app, ["run", "--headless", "hi", "-m", "gpt-5.4", "-p", "openai"])

    assert result.exit_code == 0
    assert "Downloading" not in result.stderr
