"""CLI entry point using Typer."""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer

from marv import __version__
from marv.config import Config
from marv.llm.factory import create_provider
from marv.runtime.agent import READ_ONLY_TOOLS
from marv.runtime.approval import ApprovalMode
from marv.runtime.attachments import (
    IMAGE_SUFFIXES,
    AttachmentError,
    AttachmentSpec,
    attachments_dir,
    load_attachments,
    parse_attachment_paths,
    parse_attachment_spec,
)
from marv.runtime.session import Session
from marv.runtime.settings import ThinkingLevel

from .goodbye import GoodbyeFacts, announce
from .headless import run_headless as _run_headless
from .sessions import fork_command, sessions_command, tree_command

if TYPE_CHECKING:
    from marv.llm.local_server import StoppedServer
    from marv.llm.provider import LLMProvider
    from marv.runtime.message import ImageAttachment
    from marv.tui.app import AgentApp

HELP_TEXT = """marv — local-first AI assistant TUI for Apple Silicon

Usage:
  marv                              interactive TUI (no subcommand)
  marv run [PROMPT] [OPTIONS]       start the agent (TUI, or --headless for one-shot)
  marv bench [OPTIONS]              measure time-to-first-token for the active model
  marv doctor [--json]              diagnose config, hub, server and extensions
  marv sessions [-n N]              list recent sessions
  marv fork [OPTIONS]               fork a session from a message and start the TUI
  marv tree [OPTIONS]               move the session leaf to an entry and start the TUI
  marv config-show                  show the resolved configuration
  marv mlx <args>...                pass through to the marv-mlx runtime CLI
  marv update [--check] [--force]   check GitHub and reinstall the latest release
  marv uninstall [OPTIONS]          remove marv and optional components
  marv version | --version          print the marv version
  marv help | -h | --help           show this help

Run `marv <command> --help` for the full option list of a command."""

# ``add_help_option=False`` drops Click's built-in ``--help`` on the root group
# only, so the eager flag below can own ``-h``/``--help``. Subcommands keep
# Typer's generated option help (``marv run --help`` and friends).
app = typer.Typer(
    name="marv",
    help="A local-first coding agent TUI",
    no_args_is_help=False,
    add_help_option=False,
)


def _print_help() -> None:
    """Print the curated top-level help text."""
    typer.echo(HELP_TEXT)


def _version_callback(value: bool) -> None:
    """Print the version and exit when ``--version`` is passed."""
    if value:
        typer.echo(f"marv {__version__}")
        raise typer.Exit()


def _help_callback(value: bool) -> None:
    """Print the curated help and exit when ``--help``/``-h`` is passed."""
    if value:
        _print_help()
        raise typer.Exit()


@app.callback(invoke_without_command=True)
def _root(
    ctx: typer.Context,
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            callback=_version_callback,
            is_eager=True,
            help="Show the version and exit.",
        ),
    ] = False,
    help_: Annotated[
        bool,
        typer.Option(
            "--help",
            "-h",
            callback=_help_callback,
            is_eager=True,
            help="Show this help and exit.",
        ),
    ] = False,
) -> None:
    """With no subcommand, launch the interactive TUI."""
    if ctx.invoked_subcommand is None:
        run()


@app.command()
def version() -> None:
    """Print the marv version."""
    typer.echo(f"marv {__version__}")


@app.command()
def help() -> None:
    """Show the curated top-level help."""
    _print_help()


def main() -> None:
    """Entry point for the CLI."""
    app()


def _create_llm_provider(config: Config) -> LLMProvider:
    """Create the configured LLM provider instance."""
    return create_provider(
        provider=config.provider,
        model=config.model,
        api_key=config.api_key,
        base_url=config.base_url,
        temperature=config.temperature,
        max_output_tokens=config.max_output_tokens,
        provider_overrides=config.provider_overrides(),
        server_manager=config.server_manager,
    )


@app.command()
def run(
    prompt: Annotated[str | None, typer.Argument(help="Initial prompt to run")] = None,
    model: Annotated[str | None, typer.Option("-m", "--model", help="Model to use")] = None,
    provider: Annotated[str | None, typer.Option("-p", "--provider", help="Provider name")] = None,
    thinking: Annotated[
        str | None,
        typer.Option(
            "-t",
            "--thinking",
            help="Thinking level: off, minimal, low, medium, high",
        ),
    ] = None,
    approval: Annotated[
        str | None,
        typer.Option(
            "--approval",
            help="Tool approval mode: off, destructive, all",
        ),
    ] = None,
    read_only: Annotated[
        bool,
        typer.Option(
            "--read-only",
            help="Narrow the tool set to read/grep/find/ls (no writes or shell)",
        ),
    ] = False,
    theme: Annotated[
        str | None,
        typer.Option(
            "--theme",
            help="TUI theme: auto, minimal, or any built-in Textual theme",
        ),
    ] = None,
    server_manager: Annotated[
        str | None,
        typer.Option(
            "--server-manager",
            help="Local server lifecycle: embedded (default) or marv-mlx",
        ),
    ] = None,
    extension: Annotated[
        list[Path] | None,
        typer.Option("-e", "--extension", help="Extension file(s) to load"),
    ] = None,
    attach: Annotated[
        list[str] | None,
        typer.Option(
            "--attach",
            help=(
                "Image or PDF to send with the prompt; append `:N` or `:N-M` to a PDF "
                "path to pick pages; may be repeated"
            ),
        ),
    ] = None,
    resume: Annotated[bool, typer.Option("-r", "--resume", help="Resume the last session")] = False,
    session: Annotated[
        Path | None, typer.Option("-s", "--session", help="Session file to load")
    ] = None,
    headless: Annotated[
        bool, typer.Option("--headless", help="Run without TUI (single prompt mode)")
    ] = False,
) -> None:
    """Start the coding agent.

    Run interactively (TUI mode) or with a single prompt (headless mode).
    """
    config = Config.load()

    thinking_level = config.thinking_level
    if thinking:
        try:
            thinking_level = ThinkingLevel(thinking.lower())
        except ValueError as err:
            typer.echo(f"Invalid thinking level: {thinking}", err=True)
            typer.echo("Valid values: off, minimal, low, medium, high", err=True)
            raise typer.Exit(1) from err

    approval_mode = config.approval_mode
    if approval:
        try:
            approval_mode = ApprovalMode(approval.lower())
        except ValueError as err:
            typer.echo(f"Invalid approval mode: {approval}", err=True)
            typer.echo("Valid values: off, destructive, all", err=True)
            raise typer.Exit(1) from err

    if server_manager and server_manager not in ("embedded", "marv-mlx"):
        typer.echo(f"Invalid server manager: {server_manager}", err=True)
        typer.echo("Valid values: embedded, marv-mlx", err=True)
        raise typer.Exit(1)

    extensions = list(config.extensions)
    if extension:
        extensions.extend(Path(ext) for ext in extension)

    effective_read_only = read_only or config.read_only

    config = Config(
        provider=provider or config.provider,
        model=model or config.model,
        api_key=config.api_key,
        base_url=config.base_url,
        context_max_tokens=config.context_max_tokens,
        max_output_tokens=config.max_output_tokens,
        temperature=config.temperature,
        thinking_level=thinking_level,
        approval_mode=approval_mode,
        read_only=effective_read_only,
        goodbye=config.goodbye,
        update_check=config.update_check,
        theme=theme or config.theme,
        server_manager=server_manager or config.server_manager,
        session_dir=config.session_dir,
        skills_dirs=config.skills_dirs,
        extensions=extensions,
        providers=config.providers,
        prompt_template_dirs=config.prompt_template_dirs,
        context_file_paths=config.context_file_paths,
        custom_system_prompt=config.custom_system_prompt,
        append_system_prompt=config.append_system_prompt,
    )

    if (prompt or headless) and not prompt:
        typer.echo("Error: Prompt required in headless mode", err=True)
        raise typer.Exit(1)

    llm_provider = _create_llm_provider(config)

    from marv.llm.server_lifecycle import install_handlers

    # Main thread: enables SIGTERM/SIGHUP teardown of the model server.
    install_handlers()

    loaded_session: Session | None = None
    if session:
        if session.exists():
            loaded_session = Session.load(session)
            typer.echo(f"Loaded session: {session}")
        else:
            typer.echo(f"Session file not found: {session}", err=True)
            raise typer.Exit(1)
    elif resume:
        loaded_session = Session.get_latest(config.session_dir)
        if loaded_session:
            typer.echo(f"Resuming session: {loaded_session.metadata.id}")
        else:
            typer.echo("No previous session found", err=True)

    store = attachments_dir(config.session_dir)
    if prompt or headless:
        assert prompt is not None  # guaranteed by the headless guard above
        prompt, attachments = _resolve_attachments(prompt, attach, store)
        _ensure_model_downloaded(llm_provider=llm_provider)
        asyncio.run(
            _run_headless(config, prompt, loaded_session, llm_provider, attachments=attachments)
        )
    else:
        # No prompt to pair `@path` tokens with, so only the `--attach` flags
        # apply; they ride the first message submitted in the TUI.
        _, attachments = _resolve_attachments("", attach, store)
        _run_tui(config, loaded_session, llm_provider, attachments=attachments)


def _resolve_attachments(
    prompt: str,
    flags: list[str] | None,
    store: Path,
) -> tuple[str, list[ImageAttachment] | None]:
    """Gather headless attachments from ``--attach`` flags and `@path` tokens.

    Returns the prompt with `@path` tokens removed alongside the loaded images.
    Flag paths are used as given; `@path` tokens are resolved against the cwd by
    :func:`parse_attachment_paths`, which also leaves anything that is not an
    image or PDF in the prompt. Both accept a `path:pages` PDF page selection.
    ``store`` is the blob directory the attachment bytes are cached in.
    Attachments are ``None`` when there is nothing to attach, so an ordinary
    run keeps its plain-text path.
    """
    cleaned, token_specs = parse_attachment_paths(prompt, Path.cwd())
    explicit = [parse_attachment_spec(flag, Path.cwd()) for flag in flags or []]
    unsupported = [
        spec.path
        for spec in explicit
        if spec.path.suffix.lower().lstrip(".") not in IMAGE_SUFFIXES | {"pdf"}
    ]
    if unsupported:
        names = ", ".join(str(p) for p in unsupported)
        typer.echo(f"Error: not an image or PDF: {names}", err=True)
        raise typer.Exit(1)

    specs: list[AttachmentSpec] = [*explicit, *token_specs]
    if not specs:
        return cleaned, None

    try:
        return cleaned, load_attachments(specs, store=store)
    except AttachmentError as err:
        typer.echo(f"Error: {err}", err=True)
        raise typer.Exit(1) from err


def _ensure_model_downloaded(llm_provider: LLMProvider) -> None:
    """Fetch a missing model for non-interactive runs, showing progress.

    Headless runs have no picker, so downloading is the only way a remembered
    model can work. Unlike the old silent startup download, the transfer is
    reported on stderr so the run is never invisibly stalled.
    """
    model = getattr(llm_provider, "model", None)
    if not model:
        return
    is_downloaded = getattr(llm_provider, "is_model_downloaded", None)
    factory = getattr(llm_provider, "download_model", None)
    if not callable(is_downloaded) or not callable(factory):
        return
    if is_downloaded(model):
        return

    download = factory(model)
    typer.echo(f"Downloading {model} (not present in the local Hugging Face hub)…", err=True)
    download.start()
    last = ""
    while not download.finished:
        download.poll()
        label = download.progress.label()
        if label != last:
            last = label
            typer.echo(f"  {label}", err=True)
        download.wait(0.25)
    download.poll()
    if download.state == "failed":
        typer.echo(f"Download failed: {download.error}", err=True)
        raise typer.Exit(1)
    typer.echo(f"Downloaded {model}", err=True)


def _stop_provider_server(llm_provider: LLMProvider) -> StoppedServer | None:
    """Unload the model server, if the provider owns one.

    Returns what the unload stopped (for the exit sign-off), or ``None`` when
    nothing was serving. Safe to call twice in one exit: the second call finds
    nothing serving and returns the record the first one made.
    """
    from marv.llm.local_server import StoppedServer

    unload = getattr(llm_provider, "unload", None)
    if callable(unload):
        record = unload()
        if isinstance(record, StoppedServer):
            return record
    record = getattr(llm_provider, "exit_record", None)
    return record if isinstance(record, StoppedServer) else None


def _server_still_running(llm_provider: LLMProvider) -> bool:
    """Whether a model server is still answering after the unload attempt."""
    is_serving = getattr(llm_provider, "is_serving", None)
    if not callable(is_serving):
        return False
    try:
        return bool(is_serving())
    except Exception:  # noqa: BLE001 - the sign-off must never fail the exit
        return False


def _announce_goodbye(
    app: AgentApp,
    llm_provider: LLMProvider,
    record: StoppedServer | None,
) -> None:
    """Print the exit sign-off for the session that just ended."""
    summary = app.exit_summary()
    announce(
        GoodbyeFacts(
            version=__version__,
            session_id=summary.session_id,
            model=record.model if record is not None else summary.model,
            port=record.port if record is not None else None,
            memory_bytes=summary.memory_bytes,
            unloaded=record is not None,
            still_running=_server_still_running(llm_provider),
        )
    )


def _run_tui(
    config: Config,
    session: Session | None,
    llm_provider: LLMProvider,
    *,
    attachments: list[ImageAttachment] | None = None,
) -> None:
    """Run the interactive TUI."""
    from marv.llm.model_download import start_catalog_refresh
    from marv.llm.server_lifecycle import install_handlers
    from marv.tui.app import AgentApp

    # Refresh the marv-curator catalog in the background. The download picker
    # reads the runtime's cached copy; the runtime only rewrites it from its own
    # TUI, so a marv-only user would otherwise see a stale list. Fire-and-forget:
    # the download panel waits for this same attempt when it opens.
    start_catalog_refresh()

    install_handlers()
    app = AgentApp(
        config,
        provider=llm_provider,
        session=session,
        initial_attachments=attachments,
    )
    # Clear any mouse-reporting mode a previously crashed app may have left on
    # (otherwise the terminal leaks stray escape sequences on mouse movement).
    if sys.stdout.isatty():
        sys.stdout.write("\x1b[?1000l\x1b[?1003l\x1b[?1006l")
        sys.stdout.flush()

    record: StoppedServer | None = None
    try:
        app.run()
    finally:
        # Unload the model even if the TUI crashed before its own cleanup ran.
        record = _stop_provider_server(llm_provider)

    # The sign-off is opt-out (``goodbye = false`` / ``AGENT_BYE=off``) and only
    # for a real terminal: piped output must not gain a cleared screen. It runs
    # after a clean exit only, so a crash never gets a cheerful farewell.
    if config.goodbye and sys.stdout.isatty():
        _announce_goodbye(app, llm_provider, record)


@app.command()
def bench(
    prompt: Annotated[
        list[str] | None,
        typer.Option("--prompt", help="Prompt to include (repeatable); overrides defaults"),
    ] = None,
    turns: Annotated[
        int | None,
        typer.Option("-n", "--turns", help="Number of default prompts to run"),
    ] = None,
    no_warmup: Annotated[
        bool,
        typer.Option("--no-warmup", help="Skip the warm-up turn (measures cold start)"),
    ] = False,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON"),
    ] = False,
    model: Annotated[str | None, typer.Option("-m", "--model", help="Model to use")] = None,
    provider: Annotated[str | None, typer.Option("-p", "--provider", help="Provider name")] = None,
) -> None:
    """Measure time-to-first-token for the active model.

    Reports TTFT p50/p90 and how much it grows per turn. A flat growth number
    means the prompt is not being re-prefilled in full each turn; a climbing
    one means it is, and that is the latency bug to fix first.
    """
    from .bench import DEFAULT_PROMPTS, bench_command

    base = Config.load()
    config = replace(base, provider=provider or base.provider, model=model or base.model)

    selected = list(prompt) if prompt else list(DEFAULT_PROMPTS)
    if turns is not None:
        if turns < 1:
            typer.echo("Error: --turns must be at least 1", err=True)
            raise typer.Exit(1)
        selected = selected[:turns]

    from marv.llm.server_lifecycle import install_handlers

    install_handlers()
    bench_command(
        config=config,
        create_llm_provider=_create_llm_provider,
        prompts=selected,
        warmup=not no_warmup,
        as_json=json_output,
    )


@app.command()
def doctor(
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON"),
    ] = False,
) -> None:
    """Diagnose config, hub, server, and extension setup.

    One command that checks the resolved config, the local Hugging Face hub,
    the model server, the download tooling, and whether configured extensions
    load, so a setup problem explains itself instead of surfacing later as a
    cryptic TUI error.
    """
    from .doctor import doctor_command

    config = Config.load()
    doctor_command(
        config=config,
        create_llm_provider=_create_llm_provider,
        as_json=json_output,
    )


@app.command()
def fork(
    session: Annotated[
        Path | None, typer.Option("-s", "--session", help="Session file to fork")
    ] = None,
    from_message: Annotated[
        str,
        typer.Option(
            "--from",
            help="Message id, id prefix, index, or 'last'/'assistant'",
        ),
    ] = "last",
) -> None:
    """Fork a session from a message and start the TUI."""
    fork_command(
        session,
        from_message,
        create_llm_provider=_create_llm_provider,
        run_tui=_run_tui,
    )


@app.command()
def tree(
    session: Annotated[
        Path | None, typer.Option("-s", "--session", help="Session file to open")
    ] = None,
    to: Annotated[
        str,
        typer.Option(
            "--to",
            help="Message id, id prefix, index, or 'last'/'assistant'",
        ),
    ] = "last",
) -> None:
    """Move the session leaf to an entry and start the TUI."""
    tree_command(
        session,
        to,
        create_llm_provider=_create_llm_provider,
        run_tui=_run_tui,
    )


@app.command()
def sessions(
    limit: Annotated[int, typer.Option("-n", "--limit", help="Number of sessions")] = 10,
) -> None:
    """List recent sessions."""
    sessions_command(limit)


@app.command(
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)
def mlx(
    ctx: typer.Context,
) -> None:
    """Run the marv-mlx runtime CLI (passes through all remaining arguments).

    `marv-mlx` is the recommended local-LLM runtime that sits underneath the
    harness. marv stays self-sufficient (it can launch the server itself), so
    this is a convenience passthrough: `marv mlx status --json` runs
    `marv-mlx status --json`.
    """
    passthrough = [arg for arg in ctx.args if arg != "--"]
    exec_mlx(passthrough)


def exec_mlx(args: list[str]) -> None:
    """Exec the `marv-mlx` runtime CLI, replacing this process.

    Raises ``typer.Exit(1)`` with a hint when the binary is not installed.
    """
    binary = shutil.which("marv-mlx")
    if binary is None:
        typer.echo(
            "marv-mlx runtime not found. Install it with:\n"
            "  brew install pavsefcik/marv-mlx/marv-mlx\n"
            "or:\n"
            "  curl -fsSL "
            "https://raw.githubusercontent.com/pavsefcik/marv-mlx/main/install.sh | sh",
            err=True,
        )
        raise typer.Exit(1)
    os.execv(binary, [binary, *args])


@app.command()
def update(
    check: Annotated[
        bool,
        typer.Option("--check", help="Only report whether a newer release exists"),
    ] = False,
    force: Annotated[
        bool,
        typer.Option("--force", help="Reinstall even when already up to date"),
    ] = False,
    yes: Annotated[
        bool,
        typer.Option("-y", "--yes", help="Install without prompting"),
    ] = False,
) -> None:
    """Check GitHub for a newer release and install it.

    The same release wheel the installer uses is reinstalled through
    ``uv tool install --force``. The running process keeps its old code, so a
    successful update takes effect the next time marv starts.
    """
    from marv.updates import fetch_latest_release, install_release, is_newer

    latest = fetch_latest_release()
    if latest is None:
        typer.echo(
            "could not check GitHub for updates (offline or API unreachable)",
            err=True,
        )
        raise typer.Exit(1)

    if not force and not is_newer(latest.version, __version__):
        typer.echo(f"marv {__version__} is up to date")
        return

    typer.echo(f"marv {latest.version} is available (you have {__version__})")
    if check:
        return
    if not yes and not typer.confirm(f"Update marv to {latest.version}?", default=False):
        typer.echo("update cancelled")
        raise typer.Exit(0)

    typer.echo(f"installing marv {latest.version}…", err=True)
    outcome = install_release(latest)
    if not outcome.ok:
        typer.echo(f"update failed (exit {outcome.returncode})", err=True)
        if outcome.stderr:
            typer.echo(outcome.stderr, err=True)
        raise typer.Exit(1)
    typer.echo(f"marv {latest.version} installed — restart marv to use it")


@app.command()
def uninstall(
    all_: Annotated[
        bool,
        typer.Option("--all", help="Remove every component"),
    ] = False,
    marv_: Annotated[
        bool,
        typer.Option("--marv", help="Remove the marv command and its cache/config"),
    ] = False,
    marv_mlx: Annotated[
        bool,
        typer.Option("--marv-mlx", help="Remove the marv-mlx runtime and its cache"),
    ] = False,
    mlx_vlm: Annotated[
        bool,
        typer.Option("--mlx-vlm", help="Remove the mlx-vlm uv tool"),
    ] = False,
    models: Annotated[
        bool,
        typer.Option("--models", help="Remove models from the Hugging Face hub"),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Only show what would be removed"),
    ] = False,
    yes: Annotated[
        bool,
        typer.Option("-y", "--yes", help="Remove without prompting"),
    ] = False,
) -> None:
    """Remove marv and its optional components.

    With no flags this prints a numbered picker at the terminal; nothing is
    selected by default. The Hugging Face hub is shared with other tools, so its
    models are only removed when explicitly chosen.
    """
    from .uninstall import (
        MARV,
        MARV_MLX,
        MLX_VLM,
        MODELS,
        confirm_removal,
        default_targets,
        prompt_select_targets,
        remove_target,
        target_summary,
    )

    targets = default_targets()
    by_key = {target.key: target for target in targets}

    chosen: list[str] = []
    if all_:
        chosen = list(by_key)
    else:
        if marv_:
            chosen.append(MARV)
        if marv_mlx:
            chosen.append(MARV_MLX)
        if mlx_vlm:
            chosen.append(MLX_VLM)
        if models:
            chosen.append(MODELS)

    if not chosen:
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            typer.echo(
                "nothing selected; pass --all, or --marv/--marv-mlx/--mlx-vlm/--models",
                err=True,
            )
            raise typer.Exit(2)
        chosen = prompt_select_targets(targets) or []
        if not chosen:
            typer.echo("cancelled")
            return

    typer.echo("The following will be removed:")
    for key in chosen:
        typer.echo(f"  {target_summary(by_key[key])}")

    if dry_run:
        typer.echo("dry run — nothing removed")
        return

    if MODELS in chosen:
        typer.echo(
            "warning: the Hugging Face hub is shared — this deletes models used by other tools too",
            err=True,
        )
    if MARV in chosen:
        typer.echo("note: the running marv process keeps working until you exit", err=True)

    if not yes and not confirm_removal("Remove the selected components?"):
        typer.echo("cancelled")
        return

    failed = False
    for key in chosen:
        result = remove_target(by_key[key])
        status = "removed" if result.removed else "failed"
        failed = failed or not result.removed
        typer.echo(f"  {status}: {result.label} — {result.detail}")

    if failed:
        raise typer.Exit(1)


@app.command()
def config_show() -> None:
    """Show current configuration."""
    config = Config.load()
    model_display = config.model or "[provider default]"

    typer.echo("Current configuration:")
    typer.echo(f"  Provider: {config.provider}")
    typer.echo(f"  Model: {model_display}")
    typer.echo(f"  API Key: {'[set]' if config.api_key else '[not set]'}")
    typer.echo(f"  Base URL: {config.base_url or '[default]'}")
    typer.echo(f"  Context Tokens: {config.context_max_tokens}")
    typer.echo(f"  Max Output Tokens: {config.max_output_tokens}")
    typer.echo(f"  Temperature: {config.temperature}")
    typer.echo(f"  Thinking Level: {config.thinking_level}")
    tools = ", ".join(READ_ONLY_TOOLS) if config.read_only else "[all registered]"
    typer.echo(f"  Read Only: {config.read_only}")
    typer.echo(f"  Active Tools: {tools}")
    typer.echo(f"  Theme: {config.theme}")
    typer.echo(f"  Update Check: {config.update_check}")
    typer.echo(f"  Server Manager: {config.server_manager}")
    typer.echo(f"  Session Dir: {config.session_dir}")
    typer.echo(f"  Skills Dirs: {config.skills_dirs or '[none]'}")
    typer.echo(f"  Extensions: {config.extensions or '[none]'}")

    if config.providers:
        typer.echo("\nConfigured providers:")
        for name, prov in config.providers.items():
            typer.echo(f"  {name}:")
            typer.echo(f"    Base URL: {prov.base_url}")
            typer.echo(f"    Model: {prov.model}")


if __name__ == "__main__":
    main()
