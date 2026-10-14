"""Behavior tests for `marv uninstall` selection, sizing, and removal."""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

from typer.testing import CliRunner

from marv import cli
from marv.cli.uninstall import (
    GUM_ACCENT,
    MARV,
    MARV_MLX,
    MLX_VLM,
    UninstallTarget,
    confirm_removal,
    default_targets,
    format_size,
    marv_mlx_paths,
    path_size,
    prompt_select_targets,
    remove_target,
    target_label,
    target_summary,
)

if TYPE_CHECKING:
    from pathlib import Path


def make_target(
    temp_dir: Path,
    *,
    key: str = MARV,
    filename: str = "file",
    command: tuple[str, ...] | None = None,
) -> UninstallTarget:
    """A target whose single path is a real directory in ``temp_dir``."""
    cache = temp_dir / f"{key}-cache"
    cache.mkdir()
    (cache / filename).write_text("payload", encoding="utf-8")
    return UninstallTarget(
        key=key,
        label=f"{key} label",
        description="test target",
        paths=(cache,),
        command=command,
    )


def test_default_targets_cover_marv_runtime_engine_and_models(temp_dir):
    home = temp_dir / "home"
    targets = default_targets(home=home, marv_mlx_dir=home / ".marv-mlx")

    assert {target.key for target in targets} == {"marv", "marv-mlx", "mlx-vlm", "models"}
    mlx = next(target for target in targets if target.key == "marv-mlx")
    assert home / ".marv-mlx" in mlx.paths
    assert mlx.rc_file == home / ".zshrc"


def test_target_summary_for_a_command_only_target_has_no_size(temp_dir):
    target = UninstallTarget(
        key="mlx-vlm",
        label="mlx-vlm (uv tool)",
        description="the mlx-vlm engine",
        paths=(),
        command=("uv", "tool", "uninstall", "mlx-vlm"),
    )

    assert "0 B" not in target_summary(target)
    assert target_label(target) == "mlx-vlm (uv tool)"


def test_marv_mlx_target_strips_the_install_sh_block_from_zshrc(temp_dir):
    home = temp_dir / "home"
    home.mkdir()
    zshrc = home / ".zshrc"
    zshrc.write_text(
        "export PATH=$PATH\n\n"
        "# marv-mlx (installed by install.sh)\n"
        'test -f "/x/marv-mlx-launcher.zsh" && source "/x/marv-mlx-launcher.zsh"\n'
        "alias ll='ls -l'\n",
        encoding="utf-8",
    )
    targets = default_targets(home=home, marv_mlx_dir=home / ".marv-mlx", which=lambda _n: None)
    mlx = next(target for target in targets if target.key == MARV_MLX)

    def runner(argv, **_kwargs):
        return subprocess.CompletedProcess(list(argv), 0, "", "")

    result = remove_target(mlx, runner=runner)

    text = zshrc.read_text(encoding="utf-8")
    assert "marv-mlx-launcher.zsh" not in text
    assert "installed by install.sh" not in text
    assert "export PATH=$PATH" in text
    assert "alias ll='ls -l'" in text
    assert "cleaned" in result.detail


def test_mlx_vlm_target_uninstalls_the_uv_tool(temp_dir, monkeypatch):
    home = temp_dir / "home"
    home.mkdir()
    targets = default_targets(home=home, which=lambda _n: None)
    engine = next(target for target in targets if target.key == MLX_VLM)
    seen: list[list[str]] = []
    monkeypatch.setattr("marv.cli.uninstall.shutil.which", lambda name: f"/usr/bin/{name}")

    def runner(argv, **_kwargs):
        seen.append(list(argv))
        return subprocess.CompletedProcess(list(argv), 0, "", "")

    result = remove_target(engine, runner=runner)

    assert seen == [["uv", "tool", "uninstall", "mlx-vlm"]]
    assert result.removed is True
    assert "uv tool uninstall mlx-vlm" in result.detail


def test_marv_mlx_paths_find_pi_launcher_and_extension(temp_dir):
    home = temp_dir / "home"
    launcher = home / ".pi" / "agent" / "bin" / "marv-mlx"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("#!/bin/sh\n", encoding="utf-8")
    extension = home / ".pi" / "agent" / "extensions" / "marv-mlx.ts"
    extension.parent.mkdir(parents=True)
    extension.write_text("// ext", encoding="utf-8")

    paths = marv_mlx_paths(home, which=lambda _name: str(launcher), env={})

    assert launcher in paths
    assert extension in paths


def test_marv_mlx_paths_skip_a_homebrew_shim(temp_dir):
    home = temp_dir / "home"
    home.mkdir()

    paths = marv_mlx_paths(home, which=lambda _name: "/opt/homebrew/bin/marv-mlx", env={})

    assert paths == ()


def test_marv_mlx_paths_honour_an_explicit_override(temp_dir):
    home = temp_dir / "home"
    home.mkdir()
    explicit = home / ".marv-mlx"

    paths = marv_mlx_paths(home, explicit=explicit, which=lambda _name: None, env={})

    assert paths == (explicit,)


def test_path_size_sums_nested_files_and_tolerates_missing(temp_dir):
    root = temp_dir / "root"
    nested = root / "nested"
    nested.mkdir(parents=True)
    (root / "a").write_bytes(b"12345")
    (nested / "b").write_bytes(b"123")

    assert path_size(root) == 8
    assert path_size(temp_dir / "missing") == 0


def test_format_size_uses_human_units():
    assert format_size(0) == "0 B"
    assert format_size(999) == "999 B"
    assert format_size(1500) == "1.5 KB"


def test_target_summary_mentions_the_size_and_paths(temp_dir):
    target = make_target(temp_dir, filename="a")

    summary = target_summary(target)

    assert target.label in summary
    assert str(target.paths[0]) in summary


def test_remove_target_deletes_paths_and_reports_what_went(temp_dir):
    target = make_target(temp_dir)

    result = remove_target(target)

    assert result.removed is True
    assert not target.paths[0].exists()
    assert str(target.paths[0]) in result.detail


def test_remove_target_runs_the_uninstall_command(temp_dir, monkeypatch):
    seen: list[list[str]] = []

    def runner(argv, **_kwargs):
        seen.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr("marv.cli.uninstall.shutil.which", lambda name: f"/usr/bin/{name}")
    target = UninstallTarget(
        key=MARV,
        label="marv",
        description="d",
        paths=(),
        command=("uv", "tool", "uninstall", "marv"),
    )

    result = remove_target(target, runner=runner)

    assert result.removed is True
    assert seen == [["uv", "tool", "uninstall", "marv"]]


def test_remove_target_reports_a_failing_command_without_failing_the_target(temp_dir, monkeypatch):
    def runner(argv, **_kwargs):
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr="no such tool")

    monkeypatch.setattr("marv.cli.uninstall.shutil.which", lambda name: f"/usr/bin/{name}")
    target = UninstallTarget(
        key=MARV,
        label="marv",
        description="d",
        paths=(),
        command=("uv", "tool", "uninstall", "marv"),
    )

    result = remove_target(target, runner=runner)

    # A command the machine may not have registered (brew vs shell install) is
    # reported, but the filesystem cleanup decides success.
    assert result.removed is True
    assert "no such tool" in result.detail


def test_remove_target_dry_run_touches_nothing(temp_dir):
    target = make_target(temp_dir)

    result = remove_target(target, dry_run=True)

    assert result.removed is True
    assert target.paths[0].exists()


def test_uninstall_without_a_tty_and_no_flags_refuses(monkeypatch, temp_dir):
    target = make_target(temp_dir)
    monkeypatch.setattr("marv.cli.uninstall.default_targets", lambda **_: [target])

    result = CliRunner().invoke(cli.app, ["uninstall"])

    assert result.exit_code == 2
    assert target.paths[0].exists()


def test_uninstall_dry_run_lists_without_removing(monkeypatch, temp_dir):
    target = make_target(temp_dir)
    monkeypatch.setattr("marv.cli.uninstall.default_targets", lambda **_: [target])

    result = CliRunner().invoke(cli.app, ["uninstall", "--all", "--dry-run"])

    assert result.exit_code == 0
    assert "dry run" in result.output
    assert target.label in result.output
    assert target.paths[0].exists()


def test_uninstall_marv_yes_removes_the_selected_path(monkeypatch, temp_dir):
    target = make_target(temp_dir)
    monkeypatch.setattr("marv.cli.uninstall.default_targets", lambda **_: [target])

    result = CliRunner().invoke(cli.app, ["uninstall", "--marv", "--yes"])

    assert result.exit_code == 0
    assert "removed" in result.output
    assert not target.paths[0].exists()


def test_uninstall_mlx_vlm_flag_selects_the_uv_tool(monkeypatch, temp_dir):
    target = make_target(temp_dir, key=MLX_VLM)
    monkeypatch.setattr("marv.cli.uninstall.default_targets", lambda **_: [target])

    result = CliRunner().invoke(cli.app, ["uninstall", "--mlx-vlm", "--yes"])

    assert result.exit_code == 0
    assert not target.paths[0].exists()


def test_uninstall_can_be_cancelled_at_the_prompt(monkeypatch, temp_dir):
    target = make_target(temp_dir)
    monkeypatch.setattr("marv.cli.uninstall.default_targets", lambda **_: [target])

    result = CliRunner().invoke(cli.app, ["uninstall", "--all"], input="n\n")

    assert result.exit_code == 0
    assert "cancelled" in result.output
    assert target.paths[0].exists()


def test_prompt_selects_parsed_numbers(temp_dir):
    targets = [make_target(temp_dir, key="marv"), make_target(temp_dir, key="models")]
    lines: list[str] = []

    chosen = prompt_select_targets(
        targets,
        use_gum=False,
        input_fn=lambda _prompt: "2",
        echo=lines.append,
    )

    assert chosen == ["models"]
    assert any("▌▌ marv uninstall" in line for line in lines)
    assert any("1) marv label" in line for line in lines)


def test_prompt_accepts_a_comma_separated_list(temp_dir):
    targets = [
        make_target(temp_dir, key="marv"),
        make_target(temp_dir, key="marv-mlx"),
        make_target(temp_dir, key="models"),
    ]

    chosen = prompt_select_targets(
        targets,
        use_gum=False,
        input_fn=lambda _prompt: "1, 3",
        echo=lambda _line: None,
    )

    assert chosen == ["marv", "models"]


def test_prompt_all_selects_every_target(temp_dir):
    targets = [make_target(temp_dir, key="marv"), make_target(temp_dir, key="models")]

    chosen = prompt_select_targets(
        targets,
        use_gum=False,
        input_fn=lambda _prompt: "a",
        echo=lambda _line: None,
    )

    assert chosen == ["marv", "models"]


def test_prompt_empty_input_cancels(temp_dir):
    targets = [make_target(temp_dir, key="marv")]

    assert (
        prompt_select_targets(
            targets,
            use_gum=False,
            input_fn=lambda _prompt: "",
            echo=lambda _line: None,
        )
        is None
    )


def test_prompt_ignores_out_of_range_and_invalid_tokens(temp_dir):
    targets = [make_target(temp_dir, key="marv")]
    lines: list[str] = []

    chosen = prompt_select_targets(
        targets,
        use_gum=False,
        input_fn=lambda _prompt: "9, nope, 1",
        echo=lines.append,
    )

    assert chosen == ["marv"]
    assert any("ignoring invalid choice: 9" in line for line in lines)
    assert any("ignoring invalid choice: nope" in line for line in lines)


class GumFakeRunner:
    """Fake subprocess runner that answers gum style/choose/confirm."""

    def __init__(
        self,
        selection: str = "",
        *,
        style_fails: bool = False,
        confirm_returns: bool = True,
    ) -> None:
        self.selection = selection
        self.style_fails = style_fails
        self.confirm_returns = confirm_returns
        self.calls: list[list[str]] = []
        self.kwargs: list[dict] = []

    def __call__(self, argv, **kwargs):
        argv = list(argv)
        self.calls.append(argv)
        self.kwargs.append(dict(kwargs))
        verb = argv[1] if len(argv) > 1 else ""
        if verb == "style":
            code = 1 if self.style_fails else 0
            out = "" if self.style_fails else argv[-1] + "\n"
            return subprocess.CompletedProcess(argv, code, out, "")
        if verb == "confirm":
            return subprocess.CompletedProcess(argv, 0 if self.confirm_returns else 1, "", "")
        return subprocess.CompletedProcess(argv, 0, self.selection, "")


def test_gum_picker_styles_the_wordmark_like_marv_mlx(temp_dir):
    targets = [make_target(temp_dir, key="marv"), make_target(temp_dir, key="models")]
    runner = GumFakeRunner(selection="models\n")

    chosen = prompt_select_targets(targets, use_gum=True, runner=runner)

    assert chosen == ["models"]
    choose = next(call for call in runner.calls if call[1] == "choose")
    assert "--no-limit" in choose
    assert "--label-delimiter" in choose
    wordmark = next(
        call
        for call in runner.calls
        if call[1] == "style" and any("marv uninstall" in part for part in call)
    )
    assert wordmark[wordmark.index("--foreground") + 1] == GUM_ACCENT
    assert "--bold" in wordmark


def test_gum_picker_remove_all_expands_to_every_target(temp_dir):
    targets = [make_target(temp_dir, key="marv"), make_target(temp_dir, key="models")]
    runner = GumFakeRunner(selection="all\n")

    chosen = prompt_select_targets(targets, use_gum=True, runner=runner)

    assert chosen == ["marv", "models"]


def test_gum_picker_cancel_returns_none(temp_dir):
    targets = [make_target(temp_dir, key="marv")]

    def runner(argv, **_kwargs):
        return subprocess.CompletedProcess(list(argv), 1, "", "")

    assert prompt_select_targets(targets, use_gum=True, runner=runner) is None


def test_gum_picker_falls_back_to_the_plain_prompt_when_gum_missing(temp_dir):
    targets = [make_target(temp_dir, key="marv")]

    def runner(argv, **_kwargs):
        raise FileNotFoundError("gum")

    chosen = prompt_select_targets(
        targets,
        use_gum=True,
        runner=runner,
        input_fn=lambda _prompt: "1",
        echo=lambda _line: None,
    )

    assert chosen == ["marv"]


def test_gum_picker_never_hides_stderr_where_gum_draws_the_tui(temp_dir):
    targets = [make_target(temp_dir, key="marv"), make_target(temp_dir, key="models")]
    runner = GumFakeRunner(selection="models\n")

    prompt_select_targets(targets, use_gum=True, runner=runner)

    index = next(i for i, call in enumerate(runner.calls) if call[1] == "choose")
    kwargs = runner.kwargs[index]
    assert kwargs["stdout"] == subprocess.PIPE
    assert kwargs.get("stderr") is None  # not a pipe: gum draws on stderr


def test_gum_picker_accepts_labels_as_well_as_keys(temp_dir):
    targets = [make_target(temp_dir, key="marv"), make_target(temp_dir, key="models")]
    runner = GumFakeRunner(selection=target_label(targets[1]) + "\n")

    chosen = prompt_select_targets(targets, use_gum=True, runner=runner)

    assert chosen == ["models"]


def test_gum_picker_remove_all_by_label_expands(temp_dir):
    targets = [make_target(temp_dir, key="marv"), make_target(temp_dir, key="models")]
    runner = GumFakeRunner(selection="remove all\n")

    chosen = prompt_select_targets(targets, use_gum=True, runner=runner)

    assert chosen == ["marv", "models"]


def test_confirm_removal_uses_gum_confirm(temp_dir):
    runner = GumFakeRunner(confirm_returns=True)

    assert confirm_removal("Remove the selected components?", use_gum=True, runner=runner) is True
    assert runner.calls[0][:2] == ["gum", "confirm"]


def test_confirm_removal_reports_a_gum_decline(temp_dir):
    runner = GumFakeRunner(confirm_returns=False)

    assert confirm_removal("Remove?", use_gum=True, runner=runner) is False
