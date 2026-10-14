"""`marv uninstall` — choose what to remove, sizes first, nothing by default.

The picker is plain stdin/stdout printed at the terminal's top-left, so the
command reads like every other line the terminal shows instead of taking over
the screen. The removal logic is separated from the prompt so it can be tested
(and used headlessly through flags) without a terminal. marv itself is a
``uv tool`` install, the runtime lives in ``~/.marv-mlx``, and models live in
the shared Hugging Face hub, so each is an independent choice. The hub in
particular is shared with other tools: it is never touched unless asked.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import typer

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

#: Target keys, also used by the CLI flags.
MARV = "marv"
MARV_MLX = "marv-mlx"
MLX_VLM = "mlx-vlm"
MODELS = "models"

#: UI-only aggregate row that selects every target.
REMOVE_ALL = "all"

#: The external picker marv-mlx uses; optional here.
GUM_BINARY = "gum"


@dataclass(frozen=True, slots=True)
class UninstallTarget:
    """One removable piece, with the paths it owns."""

    key: str
    label: str
    description: str
    paths: tuple[Path, ...]
    command: tuple[str, ...] | None = None
    #: Shell rc file (e.g. ``~/.zshrc``) to strip marv-mlx's generated lines from.
    rc_file: Path | None = None
    #: Lines containing any of these substrings are removed from ``rc_file``.
    rc_needles: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RemovalResult:
    """What happened for one target."""

    key: str
    label: str
    removed: bool
    detail: str


def marv_mlx_paths(
    home: Path,
    *,
    explicit: Path | None = None,
    which: Callable[[str], str | None] = shutil.which,
    env: Mapping[str, str] | None = None,
) -> tuple[Path, ...]:
    """Locate everything a marv-mlx install owns on this machine.

    marv-mlx can arrive several ways: the curl/`MARV_MLX_DIR` checkout
    (``~/.marv-mlx``), a pi-managed copy (``~/.local/share/marv-mlx`` plus the
    ``marv-mlx`` launcher and extension under ``~/.pi``), or Homebrew. Looking
    only at the default directory reported ``0 B`` and removed nothing for the
    pi-managed case, so discover each of them and skip system-installed
    binaries that Homebrew owns.
    """
    environment = os.environ if env is None else env
    paths: list[Path] = []

    # An explicit override is honoured even before it exists, so `--marv-mlx`
    # reports the intended location instead of silently nothing.
    if explicit is not None:
        paths.append(explicit)
    if environment.get("MARV_MLX_DIR"):
        paths.append(Path(environment["MARV_MLX_DIR"]))

    for candidate in (
        home / ".marv-mlx",
        home / ".local" / "share" / "marv-mlx",
        home / ".cache" / "marv-mlx",
        home / ".pi" / "agent" / "extensions" / "marv-mlx.ts",
        home / ".pi" / "agent" / "bin" / "ymlx",
    ):
        if candidate.exists() and candidate not in paths:
            paths.append(candidate)

    binary = which("marv-mlx")
    if binary:
        resolved = Path(binary)
        # A Homebrew shim is owned by the Cellar; ``brew uninstall`` handles it.
        if home in resolved.parents and resolved not in paths:
            paths.append(resolved)

    return tuple(paths)


def default_targets(
    *,
    home: Path | None = None,
    marv_mlx_dir: Path | None = None,
    which: Callable[[str], str | None] = shutil.which,
    env: Mapping[str, str] | None = None,
) -> list[UninstallTarget]:
    """Build the standard targets for this machine."""
    home = home or Path.home()
    mlx_paths = marv_mlx_paths(home, explicit=marv_mlx_dir, which=which, env=env)

    return [
        UninstallTarget(
            key=MARV,
            label="marv + cache",
            description="the marv command and its cache/config",
            paths=(home / ".cache" / "marv", home / ".marv"),
            command=("uv", "tool", "uninstall", "marv"),
        ),
        UninstallTarget(
            key=MARV_MLX,
            label="marv-mlx + cache",
            description="the marv-mlx runtime, its cache, and its shell wiring",
            paths=mlx_paths,
            command=("brew", "uninstall", "marv-mlx"),
            rc_file=home / ".zshrc",
            rc_needles=("marv-mlx-launcher.zsh", "# marv-mlx (installed by install.sh)"),
        ),
        UninstallTarget(
            key=MLX_VLM,
            label="mlx-vlm (uv tool)",
            description="the mlx-vlm inference engine installed with uv",
            paths=(),
            command=("uv", "tool", "uninstall", "mlx-vlm"),
        ),
        UninstallTarget(
            key=MODELS,
            label="Hugging Face models",
            description="all models in the shared Hugging Face hub",
            paths=(home / ".cache" / "huggingface" / "hub",),
        ),
    ]


def path_size(path: Path) -> int:
    """Total bytes under ``path`` (0 when it does not exist)."""
    try:
        if path.is_symlink() or path.is_file():
            return path.stat().st_size
        if not path.is_dir():
            return 0
    except OSError:
        return 0

    total = 0
    for child in path.rglob("*"):
        try:
            if child.is_file() and not child.is_symlink():
                total += child.stat().st_size
        except OSError:
            continue
    return total


def format_size(num_bytes: int) -> str:
    """Human-readable size (decimal units, one decimal place)."""
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1000 or unit == "TB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1000
    return f"{size:.1f} TB"


def target_summary(target: UninstallTarget) -> str:
    """One-line label with total size and the paths it covers."""
    paths = ", ".join(str(path) for path in target.paths)
    if not paths:
        return f"{target.label} — {target.description}"
    size = format_size(sum(path_size(path) for path in target.paths))
    return f"{target.label} ({size}) — {target.description}: {paths}"


def target_label(target: UninstallTarget) -> str:
    """Short label with total size, for the picker's rows."""
    if not target.paths:
        return target.label
    size = format_size(sum(path_size(path) for path in target.paths))
    return f"{target.label} ({size})"


def _run_command(
    command: Sequence[str],
    runner: Callable[..., subprocess.CompletedProcess[str]],
) -> tuple[str | None, str | None]:
    """Run an uninstall command.

    Returns ``(note, error)``: a note for the run (or a missing binary), and an
    error string when the command failed. A missing binary is not an error.
    """
    if shutil.which(command[0]) is None:
        return f"{command[0]} not on PATH; skipped", None
    try:
        completed = runner(list(command), check=False, capture_output=True, text=True)
    except OSError as exc:
        return None, str(exc)
    if completed.returncode != 0:
        return None, (completed.stderr or completed.stdout or "").strip() or "command failed"
    return " ".join(command), None


def _strip_rc_lines(
    path: Path,
    needles: Sequence[str],
    *,
    dry_run: bool = False,
) -> str | None:
    """Drop the generated marv-mlx lines from a shell rc file."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    lines = text.splitlines(keepends=True)
    kept = [line for line in lines if not any(needle in line for needle in needles)]
    if len(kept) == len(lines):
        return None
    if not dry_run:
        try:
            path.write_text("".join(kept), encoding="utf-8")
        except OSError as exc:
            return f"{path}: {exc}"
    return f"cleaned {path}"


def remove_target(
    target: UninstallTarget,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    dry_run: bool = False,
) -> RemovalResult:
    """Remove one target's command, paths, and shell wiring.

    A failing uninstall command (for example ``brew uninstall marv-mlx`` when
    the runtime was installed by the shell script) is reported but does not
    fail the target: the filesystem removal is what decides success.
    """
    notes: list[str] = []
    removed: list[str] = []
    errors: list[str] = []

    if target.command is not None:
        if dry_run:
            notes.append(" ".join(target.command))
        else:
            note, error = _run_command(target.command, runner)
            if note:
                notes.append(note)
            if error:
                notes.append(error)

    if target.rc_file is not None and target.rc_needles:
        rc_note = _strip_rc_lines(target.rc_file, target.rc_needles, dry_run=dry_run)
        if rc_note:
            notes.append(rc_note)

    for path in target.paths:
        try:
            exists = path.exists() or path.is_symlink()
        except OSError:
            exists = False
        if not exists:
            continue
        if dry_run:
            removed.append(str(path))
            continue
        try:
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            else:
                path.unlink()
            removed.append(str(path))
        except OSError as exc:
            errors.append(f"{path}: {exc}")

    parts = removed + notes + errors
    detail = "; ".join(parts) if parts else "nothing to remove"
    return RemovalResult(target.key, target.label, removed=not errors, detail=detail)


def prompt_select_targets(
    targets: list[UninstallTarget],
    *,
    input_fn: Callable[[str], str] = input,
    echo: Callable[[str], None] = typer.echo,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    use_gum: bool | None = None,
) -> list[str] | None:
    """Multi-select for the removable pieces, gum-styled when available.

    With ``gum`` on PATH (the marv-mlx look: magenta wordmark, ``gum choose
    --no-limit``) the picker is a styled interactive list. Without it — or when
    it cannot run — the same choice is printed as a plain numbered prompt, so
    the command never depends on an external binary. ``use_gum`` forces either
    path; the other arguments are test seams.
    """
    if use_gum is None:
        use_gum = gum_available() and sys.stdin.isatty() and sys.stdout.isatty()

    if use_gum:
        try:
            return _gum_select(targets, runner=runner)
        except OSError:
            pass
    return _plain_select(targets, input_fn=input_fn, echo=echo)


def _plain_select(
    targets: list[UninstallTarget],
    *,
    input_fn: Callable[[str], str],
    echo: Callable[[str], None],
) -> list[str] | None:
    """Inline numbered multi-select, printed at the terminal's top-left."""
    echo("")
    echo("  ▌▌ marv uninstall")
    echo("")
    echo("  what would you like to remove?")
    echo("")
    for index, target in enumerate(targets, start=1):
        echo(f"    {index}) {target_label(target)}")
        paths = ", ".join(str(path) for path in target.paths)
        echo(f"       {target.description}: {paths}")
    echo("")
    try:
        raw = input_fn("  select [1,3 · a for all · empty to cancel]: ").strip()
    except (EOFError, KeyboardInterrupt):
        return None

    if not raw or raw.lower() in {"q", "quit", "cancel"}:
        return None
    if raw.lower() in {"a", "all", "*"}:
        return [target.key for target in targets]

    chosen: list[str] = []
    for token in re.split(r"[,\s]+", raw):
        if not token:
            continue
        if not token.isdigit() or not 1 <= int(token) <= len(targets):
            echo(f"  ignoring invalid choice: {token}")
            continue
        key = targets[int(token) - 1].key
        if key not in chosen:
            chosen.append(key)
    return chosen or None


#: The marv-mlx palette: magenta wordmark, grey subtitles.
GUM_ACCENT = "212"
GUM_MUTED = "244"


def gum_available() -> bool:
    """Whether the ``gum`` binary is on PATH."""
    return shutil.which(GUM_BINARY) is not None


def _gum_style(
    text: str,
    color: str,
    *,
    bold: bool = False,
    runner: Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    """Print ``text`` styled by ``gum style`` straight to the terminal.

    Output is not captured: gum strips ANSI when stdout is a pipe, which would
    silently drop the marv-mlx colours. A missing gum falls back to plain text.
    """
    argv = [GUM_BINARY, "style", "--foreground", color]
    if bold:
        argv.append("--bold")
    argv.append(text)
    try:
        runner(argv, check=False, stdout=None, stderr=None)
    except OSError:
        typer.echo(text)


def _gum_select(
    targets: list[UninstallTarget],
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]],
) -> list[str] | None:
    """Run ``gum choose --no-limit``; return chosen keys, or None on cancel."""
    # marv-mlx opens on a fresh screen: clear and home the cursor first.
    sys.stdout.write("\033[2J\033[H\n")
    sys.stdout.flush()
    _gum_style("▌▌ marv uninstall", GUM_ACCENT, bold=True, runner=runner)
    _gum_style("what would you like to remove?", GUM_MUTED, runner=runner)
    typer.echo("")

    options = [(target_label(target), target.key) for target in targets]
    options.append(("remove all", REMOVE_ALL))
    lines = [f"{label}\t{key}" for label, key in options]

    completed = runner(
        [
            GUM_BINARY,
            "choose",
            "--no-limit",
            "--height",
            str(len(lines) + 2),
            "--header",
            "",
            "--cursor.foreground",
            GUM_ACCENT,
            "--selected.foreground",
            GUM_ACCENT,
            "--label-delimiter",
            "\t",
        ],
        input="\n".join(lines),
        check=False,
        # gum draws its TUI on stderr. Capturing it (capture_output=True) hides
        # the picker while it waits for keys, so only stdout is piped.
        stdout=subprocess.PIPE,
        stderr=None,
        text=True,
    )
    if completed.returncode != 0:
        return None
    selected = [line for line in (completed.stdout or "").splitlines() if line.strip()]
    return _expand_selection(selected, targets, options)


def _expand_selection(
    selected: list[str],
    targets: list[UninstallTarget],
    options: list[tuple[str, str]],
) -> list[str]:
    """Map the picker's output to target keys.

    ``gum choose --label-delimiter`` returns the value half (the key), but this
    accepts the label too so a version that returns either cannot break the
    command. The aggregate row selects everything.
    """
    valid = {target.key for target in targets}
    label_to_key = {label: key for label, key in options}
    mapped: list[str] = []
    for item in selected:
        if item == REMOVE_ALL or label_to_key.get(item) == REMOVE_ALL:
            return [target.key for target in targets]
        key = item if item in valid else label_to_key.get(item)
        if key in valid and key not in mapped:
            mapped.append(key)
    return [target.key for target in targets if target.key in mapped]


def confirm_removal(
    prompt: str,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    use_gum: bool | None = None,
) -> bool:
    """Yes/no confirmation, ``gum confirm`` when available (marv-mlx look)."""
    if use_gum is None:
        use_gum = gum_available() and sys.stdin.isatty() and sys.stdout.isatty()
    if use_gum:
        try:
            completed = runner(
                [GUM_BINARY, "confirm", prompt],
                check=False,
            )
        except OSError:
            pass
        else:
            return completed.returncode == 0
    return bool(typer.confirm(prompt, default=False))
