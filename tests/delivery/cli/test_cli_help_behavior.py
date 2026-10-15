"""Behavior tests for the curated CLI help surface (`marv help`)."""

from __future__ import annotations

from typer.testing import CliRunner

from marv import __version__, cli


def test_marv_help_lists_real_commands_with_their_descriptions():
    result = CliRunner().invoke(cli.app, ["help"])

    assert result.exit_code == 0
    assert "marv — local-first" in result.stdout
    for listing in (
        "marv run [PROMPT] [OPTIONS]",
        "marv bench [OPTIONS]",
        "marv doctor [--json]",
        "marv sessions [-n N]",
        "marv fork [OPTIONS]",
        "marv tree [OPTIONS]",
        "marv config-show",
        "marv mlx <args>...",
        "marv update [--check] [--force]",
        "marv uninstall [OPTIONS]",
        "marv version | --version",
        "marv help | -h | --help",
    ):
        assert listing in result.stdout
    assert "start the agent" in result.stdout
    assert "diagnose config, hub, server and extensions" in result.stdout
    assert "list recent sessions" in result.stdout


def test_root_help_flags_print_exactly_the_help_command_text():
    runner = CliRunner()
    expected = runner.invoke(cli.app, ["help"]).stdout

    for flag in ("--help", "-h"):
        result = runner.invoke(cli.app, [flag])
        assert result.exit_code == 0
        assert result.stdout == expected, flag


def test_run_help_keeps_the_full_typer_option_help():
    result = CliRunner().invoke(cli.app, ["run", "--help"])

    assert result.exit_code == 0
    assert "Usage: marv run" in result.stdout
    assert "--headless" in result.stdout
    assert "--approval" in result.stdout


def test_bench_help_keeps_the_full_typer_option_help():
    result = CliRunner().invoke(cli.app, ["bench", "--help"])

    assert result.exit_code == 0
    assert "Usage: marv bench" in result.stdout
    assert "--no-warmup" in result.stdout
    assert "--turns" in result.stdout


def test_version_command_prints_the_marv_version():
    result = CliRunner().invoke(cli.app, ["version"])

    assert result.exit_code == 0
    assert result.stdout.strip() == f"marv {__version__}"


def test_version_flag_still_prints_the_marv_version():
    result = CliRunner().invoke(cli.app, ["--version"])

    assert result.exit_code == 0
    assert result.stdout.strip() == f"marv {__version__}"


def test_unknown_command_still_errors():
    result = CliRunner().invoke(cli.app, ["frobnicate"])

    assert result.exit_code != 0
    assert "No such command" in result.stderr
