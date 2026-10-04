"""CLI entry point using Typer."""

import asyncio
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer

from marv import __version__
from marv.config import Config
from marv.llm.factory import create_provider
from marv.runtime.approval import ApprovalMode
from marv.runtime.session import Session
from marv.runtime.settings import ThinkingLevel

from .headless import run_headless as _run_headless
from .sessions import fork_command, sessions_command, tree_command

if TYPE_CHECKING:
    from marv.llm.provider import LLMProvider

app = typer.Typer(
    name="marv",
    help="A local-first coding agent TUI",
    no_args_is_help=False,
)


def _version_callback(value: bool) -> None:
    """Print the version and exit when ``--version`` is passed."""
    if value:
        typer.echo(f"marv {__version__}")
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
) -> None:
    """With no subcommand, launch the interactive TUI."""
    if ctx.invoked_subcommand is None:
        run()


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
    theme: Annotated[
        str | None,
        typer.Option(
            "--theme",
            help="TUI theme: auto, minimal, or any built-in Textual theme",
        ),
    ] = None,
    extension: Annotated[
        list[Path] | None,
        typer.Option("-e", "--extension", help="Extension file(s) to load"),
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

    extensions = list(config.extensions)
    if extension:
        extensions.extend(Path(ext) for ext in extension)

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
        theme=theme or config.theme,
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

    if prompt or headless:
        assert prompt is not None  # guaranteed by the headless guard above
        _ensure_model_downloaded(llm_provider=llm_provider)
        asyncio.run(_run_headless(config, prompt, loaded_session, llm_provider))
    else:
        _run_tui(config, loaded_session, llm_provider)


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


def _stop_provider_server(llm_provider: LLMProvider) -> None:
    """Unload the model server, if the provider owns one."""
    stop = getattr(llm_provider, "stop_if_serving", None)
    if callable(stop):
        stop()


def _run_tui(config: Config, session: Session | None, llm_provider: LLMProvider) -> None:
    """Run the interactive TUI."""
    from marv.llm.server_lifecycle import install_handlers
    from marv.tui.app import AgentApp

    install_handlers()
    app = AgentApp(config, provider=llm_provider, session=session)
    # Clear any mouse-reporting mode a previously crashed app may have left on
    # (otherwise the terminal leaks stray escape sequences on mouse movement).
    if sys.stdout.isatty():
        sys.stdout.write("\x1b[?1000l\x1b[?1003l\x1b[?1006l")
        sys.stdout.flush()
    try:
        app.run()
    finally:
        # Unload the model even if the TUI crashed before its own cleanup ran.
        _stop_provider_server(llm_provider)


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
