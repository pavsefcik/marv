"""Main Textual TUI application."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.theme import Theme
from textual.widgets import Static

from marv import __version__
from marv.config.state import remember_latency_fit, remember_theme
from marv.runtime.attachments import (
    AttachmentError,
    attachments_dir,
    load_attachments,
    parse_attachment_paths,
)
from marv.tui.chat import ChatView
from marv.tui.compose import TUILoaders, TUIRuntime, build_tui_loaders, build_tui_runtime
from marv.tui.controller import TUIController
from marv.tui.download_panel import DownloadPanel
from marv.tui.extension_bridge import TUIExtensionBridge
from marv.tui.extension_ui import ConfirmPanel
from marv.tui.input import PromptInput
from marv.tui.memory import read_process_tree_memory, snapshot
from marv.tui.model_panel import ModelChoice, ModelPanel
from marv.tui.renderer import TUIRenderer
from marv.tui.status import StatusBar
from marv.tui.theme import (
    AUTO_THEME,
    MARV_MLX_THEME_NAME,
    is_known_theme,
    resolve_theme_name,
    resolve_theme_name_safely,
)
from marv.tui.theme_panel import ThemePanel

if TYPE_CHECKING:
    from collections.abc import Callable

    from textual.timer import Timer

    from marv.config import Config
    from marv.extensions.host import ExtensionHost
    from marv.llm.model_download import DownloadHandle
    from marv.llm.provider import LLMProvider
    from marv.runtime.agent import Agent
    from marv.runtime.message import ImageAttachment
    from marv.runtime.session import Session
    from marv.tui.panel import DockPanel
    from marv.updates import InstallOutcome, Release

# Minimal Nord-inspired theme
MINIMAL_THEME = Theme(
    name="minimal",
    primary="#88c0d0",
    secondary="#81a1c1",
    accent="#5e81ac",
    foreground="#d8dee9",
    background="#2e3440",
    surface="#3b4252",
    panel="#434c5e",
    success="#a3be8c",
    warning="#ebcb8b",
    error="#bf616a",
    dark=True,
)

# The marv-mlx look as a theme: the magenta wordmark, dim greys and the
# green/yellow markers of the marv family UI.
MARV_MLX_THEME = Theme(
    name=MARV_MLX_THEME_NAME,
    primary="#ff87d7",
    secondary="#d7afd7",
    accent="#af87ff",
    foreground="#e4e4e4",
    background="#1c1c1c",
    surface="#262626",
    panel="#303030",
    success="#00d787",
    warning="#ffff5f",
    error="#ff5f5f",
    dark=True,
)


@dataclass(frozen=True, slots=True)
class ExitSummary:
    """What the CLI needs for the exit sign-off after the TUI has closed."""

    session_id: str | None
    model: str | None
    memory_bytes: int


class AgentApp(App[None]):
    """Minimal TUI application for the coding agent."""

    CSS_PATH = "styles.tcss"
    TITLE = "marv"

    BINDINGS = [
        Binding("ctrl+c", "quit", "Quit"),
        Binding("ctrl+l", "clear", "Clear"),
        Binding("ctrl+o", "copy_last_reply", "Copy last reply"),
        Binding("escape", "escape_pressed", "Cancel/Focus", show=False),
    ]

    is_processing = reactive(False)

    def __init__(
        self,
        config: Config,
        provider: LLMProvider,
        session: Session | None = None,
        initial_attachments: list[ImageAttachment] | None = None,
    ) -> None:
        super().__init__()
        self._bootstrap_config = config
        self._session = session
        self._provider = provider
        #: ``--attach`` images for the TUI: they ride the first submitted
        #: message, then are cleared (there is no prompt to pair them with).
        self._initial_attachments: list[ImageAttachment] = list(initial_attachments or [])
        self._runtime: TUIRuntime | None = None
        self._loaders: TUILoaders = build_tui_loaders(config)
        self._controller = TUIController(self)
        self._extension_bridge = TUIExtensionBridge(self)
        self._renderer = TUIRenderer(self, loaders=self._loaders)
        # Appearance is probed here, before Textual owns the terminal, and the
        # resolved name is applied in on_mount (built-in themes are registered
        # by Textual already; MINIMAL_THEME is registered at mount).
        self._theme_name, self._theme_warning = resolve_theme_name(config.theme)
        #: The theme value to mark as current in the chooser (``auto``, not the
        #: concrete ``ansi-*`` it resolves to), so the choice survives a restart.
        #: An unknown configured name fell back to ``auto``, so the chooser
        #: marks that instead of nothing.
        self._theme_configured = config.theme if self._theme_warning is None else AUTO_THEME
        self._spinner_timer: Timer | None = None
        self._extension_widget_timer: Timer | None = None
        self._memory_timer: Timer | None = None
        self._active_panel: DockPanel[Any] | None = None
        self._panel_callback: Callable[[Any], None] | None = None

        self._cancel_event: asyncio.Event | None = None
        self._model_start_task: asyncio.Task[None] | None = None
        self._model_start_for: str | None = None
        #: Background GitHub release probe (never blocks startup).
        self._update_task: asyncio.Task[None] | None = None
        #: Registry file for this running instance (shared-server guard).
        self._instance_path: Path | None = None
        #: Model whose persisted wait calibration is currently installed.
        self._fit_seeded_for: str | None = None
        #: Facts for the CLI's exit sign-off, captured before the unload.
        self._exit_summary: ExitSummary | None = None

    @property
    def runtime(self) -> TUIRuntime:
        """Get the runtime stack, creating it if needed."""
        if self._runtime is None:
            self._runtime = build_tui_runtime(
                self._bootstrap_config,
                self._provider,
                self._session,
                loaders=self._loaders,
                extension_bridge=self._extension_bridge,
            )
        return self._runtime

    @property
    def agent(self) -> Agent:
        """Get the agent, creating it if needed."""
        return self.runtime.agent

    @property
    def extension_host(self) -> ExtensionHost:
        """Get the extension host, creating the agent if needed."""
        return self.runtime.extension_host

    def watch_is_processing(self, processing: bool) -> None:
        """React to processing state changes."""
        if processing:
            self._spinner_timer = self.set_interval(0.2, self._animate_spinner)
        elif self._spinner_timer:
            self._spinner_timer.stop()
            self._spinner_timer = None

    def _animate_spinner(self) -> None:
        """Animate the spinner and refresh the generating elapsed reading."""
        chat = self.query_one("#chat-view", ChatView)
        chat.advance_waiting()
        self._renderer.tick()

    def compose(self) -> ComposeResult:
        """Compose the UI."""
        with Horizontal(id="main-container"):
            yield ChatView(id="chat-view")
            yield Static("", id="extension-right-panel", classes="extension-slot hidden")
        yield Static("", id="extension-footer", classes="extension-slot hidden")
        with Vertical(id="input-container"):
            yield PromptInput(
                skill_loader=self._loaders.skill_loader,
                template_loader=self._loaders.template_loader,
                id="prompt-input",
            )
        yield StatusBar(id="status-line")

    async def on_mount(self) -> None:
        """Initialize on mount."""
        # Ensure agent is created so session model restore applies before UI renders
        _ = self.agent
        if self._session is None:
            self._session = self.agent.session

        # Register this process and warn (asynchronously) when the selected
        # model would evict a different model on the shared server.
        self._register_instance()
        asyncio.create_task(self._warn_if_shared_server())

        # Register and apply theme (built-in themes are already registered)
        self.register_theme(MINIMAL_THEME)
        self.register_theme(MARV_MLX_THEME)
        self.theme = self._theme_name

        # Show banner
        self._renderer.render_banner()

        # Fire-and-forget GitHub release probe. It never blocks startup and
        # never steals an already-open picker.
        self._update_task = asyncio.create_task(self._check_for_update())

        if self._theme_warning:
            self.query_one("#chat-view", ChatView).add_system_message(self._theme_warning)

        # Update status bar
        status = self.query_one("#status-line", StatusBar)
        status.set_model(self.agent.model_name or "(no model)")
        status.set_thinking(self.agent.thinking_level)
        status.set_session(
            self.agent.session_id,
            self.agent.session_parent_id,
        )
        status.set_extension_status(None)

        # Focus input
        self.query_one("#prompt-input", PromptInput).focus()
        self._refresh_prompt_models()

        # The wait prediction is only as good as this machine's own measured
        # turns; a first-ever run gets an empty fit and therefore no ETA.
        self.seed_latency_fit()

        # No model is selected/downloaded yet: offer whatever is already in the
        # local hub, or ask which one to fetch when the hub is empty. Either way
        # nothing is downloaded without the user asking for it.
        if not self.agent.model_name:
            if await self._installed_models():
                await self._open_model_panel()
            else:
                await self._open_download_panel()
        elif self._model_is_absent():
            # A model is remembered but is not in the local hub — this is the
            # case that used to trigger an invisible download on startup.
            chat = self.query_one("#chat-view", ChatView)
            chat.add_system_message(f"model {self.agent.model_name} is not downloaded locally")
            await self._open_download_panel(remembered=True)
        else:
            # A resumed/config model also counts as "last used", and its local
            # server is warmed up now so the first prompt runs immediately.
            self._controller.remember_selection()
            self.start_model()

        # Load extensions if configured
        if self._bootstrap_config.extensions:
            errors = await self.extension_host.load_extensions()
            chat = self.query_one("#chat-view", ChatView)
            for error in errors:
                chat.add_system_message(f"extension error: {error}")
            commands = self.extension_host.command_names()
            self.query_one("#prompt-input", PromptInput).set_extension_commands(commands)

        # Show existing messages if resuming a session
        if self._session and self._session.messages:
            self._renderer.render_session_messages(self._session)

        self._extension_widget_timer = self.set_interval(
            0.2,
            self._extension_bridge.render_widgets,
        )
        self._memory_timer = self.set_interval(2.0, self._refresh_memory)
        await self._refresh_memory()

    def _register_instance(self) -> None:
        """Record this process so a later instance can see it."""
        from marv.instance import default_instance_path, register

        path = default_instance_path()
        self._instance_path = path
        register(
            path,
            model=self.agent.model_name or None,
            provider=self._bootstrap_config.provider,
        )

    async def _warn_if_shared_server(self) -> None:
        """Warn when the selected model would evict a live instance's model."""
        try:
            warning = await asyncio.to_thread(self._shared_server_warning)
        except Exception:  # noqa: BLE001 - the guard is advisory
            return
        if warning:
            self.query_one("#chat-view", ChatView).add_system_message(warning)

    def _shared_server_warning(self) -> str | None:
        """Blocking part of the shared-server guard (network + registry)."""
        from marv.instance import active_other, conflict_warning
        from marv.llm.mlx_models import running_model_id

        other = active_other(self._instance_path) if self._instance_path else None
        served: str | None = None
        base_url = getattr(self.agent.provider, "base_url", None)
        if isinstance(base_url, str) and base_url:
            served = running_model_id(base_url)
        return conflict_warning(
            selected_model=self.agent.model_name,
            other=other,
            served_model=served,
        )

    async def _check_for_update(self) -> None:
        """Probe GitHub in the background and offer to install a newer release.

        Runs at most once a day (the probe caches its answer). A failure is
        silent: an update check must never break or stall startup.
        """
        if not self._bootstrap_config.update_check:
            return

        try:
            release = await asyncio.to_thread(self._probe_update)
        except Exception:  # noqa: BLE001 - the check is best-effort
            return
        if release is None:
            return

        chat = self.query_one("#chat-view", ChatView)
        chat.add_system_message(f"marv {release.version} is available (you have {__version__})")
        if self._active_panel is not None:
            # A picker owns the input line; do not steal it. The user can run
            # `marv update` when they are done.
            chat.add_system_message("run 'marv update' to install it")
            return

        confirmed = await self.await_panel(
            ConfirmPanel(f"Update marv to {release.version} now? (y/n)")
        )
        if not confirmed:
            return

        chat.add_system_message(f"installing marv {release.version}…")
        outcome = await asyncio.to_thread(self._install_update, release)
        if outcome.ok:
            chat.add_system_message(f"marv {release.version} installed — restart marv to use it")
        else:
            detail = outcome.stderr or f"exit {outcome.returncode}"
            chat.add_system_message(f"update failed: {detail}")

    def _probe_update(self) -> Release | None:
        """Return a newer release, or None (patchable seam for tests)."""
        from marv.updates import check_for_update

        return check_for_update(__version__)

    def _install_update(self, release: Release) -> InstallOutcome:
        """Install ``release`` (patchable seam for tests)."""
        from marv.updates import install_release

        return install_release(release)

    async def _refresh_memory(self) -> None:
        """Refresh the RAM reading in the status bar from host helpers."""
        provider = self.agent.provider
        server_pid = await asyncio.to_thread(lambda: getattr(provider, "server_pid", None))
        usage = await asyncio.to_thread(snapshot, server_pid)
        self.query_one("#status-line", StatusBar).set_memory(usage)

    @on(PromptInput.Submitted, "#prompt-input")
    async def on_input_submitted(self, event: PromptInput.Submitted) -> None:
        """Handle user input submission."""
        if self.is_processing:
            return

        prompt = event.value.strip()
        if not prompt:
            return

        self.query_one("#prompt-input", PromptInput).clear()
        chat = self.query_one("#chat-view", ChatView)

        if await self._controller.handle_prompt_command(prompt):
            return

        # Pull `@path` attachments out of the prompt before it is rendered, so
        # the chat shows the image rather than the token. Loading (and any PDF
        # render) shells out, so it runs off the event loop.
        prompt, attachment_specs = parse_attachment_paths(prompt, Path.cwd())
        attachments: list[ImageAttachment] = []
        if attachment_specs:
            store = attachments_dir(self.agent.config.session_dir)
            try:
                attachments = await asyncio.to_thread(load_attachments, attachment_specs, store)
            except AttachmentError as exc:
                chat.add_system_message(f"attachment error: {exc}")
                return
        if self._initial_attachments:
            # ``--attach`` flags lead, matching the headless ordering.
            attachments = [*self._initial_attachments, *attachments]
            self._initial_attachments = []

        # Add user message (special handling for $skill-name)
        if not self._renderer.render_skill_invocation(prompt):
            chat.add_user_message(prompt, attachments)

        if not self.agent.model_name:
            chat.add_system_message("no model selected — pick one with the picker (or /model <id>)")
            if await self._installed_models():
                await self._open_model_panel()
            else:
                await self._open_download_panel()
            return

        # Start processing - show thinking indicator if thinking is enabled
        self.is_processing = True
        thinking_enabled = (
            self.agent.thinking_level.value != "off" and self.agent.supports_thinking()
        )
        chat.start_assistant_message(thinking=thinking_enabled)

        # Run agent in background
        self._run_agent(prompt, attachments)

    def _restore_input_focus(self, _result: object = None) -> None:
        """Focus the prompt input once no menu is up."""
        if self._active_panel is not None:
            return
        self.query_one("#prompt-input", PromptInput).focus()
        self._refresh_prompt_models()

    def _refresh_prompt_models(self) -> None:
        """Refresh the model names offered by the inline `/model` dropdown."""

        async def _scan() -> None:
            try:
                models = await self._installed_models()
            except Exception:  # noqa: BLE001 - the dropdown is best-effort
                return
            prompt = self.query_one("#prompt-input", PromptInput)
            prompt.set_models(models)

        asyncio.create_task(_scan())

    async def _installed_models(self) -> list[str]:
        """Models already available locally (empty when the scan fails)."""
        try:
            return await self.agent.list_models()
        except Exception:  # noqa: BLE001 - the pickers work without the scan
            return []

    def _model_is_absent(self) -> bool:
        """Whether the selected model is missing from the local hub.

        Providers without a hub (cloud backends, generic OpenAI-compatible
        endpoints) always report the model as present, so they keep the
        existing behaviour.
        """
        provider = self.agent.provider
        model = self.agent.model_name
        if not model:
            return False
        is_downloaded = getattr(provider, "is_model_downloaded", None)
        if not callable(is_downloaded):
            return False
        try:
            return not is_downloaded(model)
        except Exception:  # noqa: BLE001 - a hub read failure must not block startup
            return False

    def model_is_available(self, model: str) -> bool:
        """Whether ``model`` can be run right now (i.e. it is downloaded)."""
        if not model:
            return False
        is_downloaded = getattr(self.agent.provider, "is_model_downloaded", None)
        if not callable(is_downloaded):
            return True
        try:
            return bool(is_downloaded(model))
        except Exception:  # noqa: BLE001 - unknown providers stay permissive
            return True

    def request_model_download(self, model: str) -> None:
        """Ask the user to download ``model`` before switching to it."""
        asyncio.create_task(self._open_download_panel(preselected=model))

    def _create_download(self, model: str) -> DownloadHandle:
        """Create (but do not start) a download for ``model``."""
        provider = self.agent.provider
        factory = getattr(provider, "download_model", None)
        if not callable(factory):
            raise RuntimeError(f"provider {self.agent.provider_name!r} cannot download models")
        return factory(model)  # type: ignore[no-any-return]

    @property
    def active_panel(self) -> DockPanel[Any] | None:
        """The menu currently replacing the prompt input line, if any."""
        return self._active_panel

    def open_panel[T](
        self,
        panel: DockPanel[T],
        callback: Callable[[T | None], None] | None = None,
    ) -> None:
        """Show a menu in the input dock, replacing the prompt line."""
        if self._active_panel is not None:
            self.close_panel(self._active_panel, None)
        self._active_panel = panel
        self._panel_callback = callback
        self.query_one("#prompt-input", PromptInput).display = False
        self.query_one("#input-container").mount(panel)

    def close_panel(self, panel: DockPanel[Any], result: Any) -> None:
        """Remove a docked menu and return to input mode."""
        if self._active_panel is not panel:
            return
        self._active_panel = None
        callback, self._panel_callback = self._panel_callback, None
        panel.remove()
        self.query_one("#prompt-input", PromptInput).display = True
        if callback is not None:
            callback(result)
        self._restore_input_focus()

    async def await_panel[T](self, panel: DockPanel[T]) -> T | None:
        """Open a menu in the input dock and await its dismissal result."""
        loop = asyncio.get_running_loop()
        future: asyncio.Future[T | None] = loop.create_future()

        def _done(result: T | None) -> None:
            if not future.done():
                future.set_result(result)

        self.open_panel(panel, callback=_done)
        return await future

    async def _open_download_panel(
        self,
        *,
        remembered: bool = False,
        preselected: str | None = None,
    ) -> None:
        """Open the download picker; the chosen model is applied on dismiss.

        The menu is callback-driven (rather than awaited) so it can be opened
        from ``on_mount`` without stalling startup.
        """
        installed = set(await self._installed_models())

        self.open_panel(
            DownloadPanel(
                create_download=self._create_download,
                installed=installed,
                preselected=preselected if preselected is not None else self.agent.model_name,
                remembered=remembered,
            ),
            callback=self._on_download_panel_closed,
        )

    def _on_download_panel_closed(self, model: str | None) -> None:
        """Apply the model chosen in the download picker, then refocus input."""
        if model:
            self._controller.switch_model(model)
        self._restore_input_focus()

    async def _open_model_panel(self) -> None:
        """Open the model menu; a choice is applied when it closes."""
        models = await self._installed_models()
        self.open_panel(
            ModelPanel(self.agent, models=models),
            callback=self._on_model_panel_closed,
        )

    def _on_model_panel_closed(self, choice: ModelChoice | None) -> None:
        """Apply the change chosen in the model menu."""
        if choice is not None:
            self._controller.on_model_choice(choice.model, choice.thinking)

    def open_theme_panel(self) -> None:
        """Open the theme menu; a choice is applied when it closes."""
        self.open_panel(
            ThemePanel(self._theme_configured),
            callback=self._on_theme_panel_closed,
        )

    def _on_theme_panel_closed(self, choice: str | None) -> None:
        """Apply and persist the theme chosen in the menu. Cancel keeps it."""
        if choice is not None:
            self.set_theme(choice, persist=True)

    def apply_theme(self, name: str) -> None:
        """Apply a theme by name (``/theme <name>``); unknown names are ignored."""
        candidate = name.strip()
        if not is_known_theme(candidate):
            self.query_one("#chat-view", ChatView).add_system_message(f"unknown theme: {candidate}")
            return
        self.set_theme(candidate, persist=True)

    def set_theme(self, name: str, *, persist: bool = False) -> None:
        """Apply a theme, optionally remembering it as the last used one."""
        self.theme = resolve_theme_name_safely(name)
        self._theme_name = self.theme
        self._theme_configured = name
        if persist:
            remember_theme(self.agent.config.session_dir, name)
        shown = f"auto ({self.theme})" if name == AUTO_THEME else name
        self.notify(f"theme: {shown}")

    def _run_agent(self, prompt: str, attachments: list[ImageAttachment] | None = None) -> None:
        """Run the agent loop."""
        self._cancel_event = asyncio.Event()
        asyncio.create_task(self._agent_worker(prompt, attachments))

    def start_model(self) -> asyncio.Task[None] | None:
        """Kick off (or return) the single-flight task that starts the selected
        model's local server. Returns None if there is nothing to start (no
        model, already serving, or a provider without a server lifecycle)."""
        provider = self.agent.provider
        ensure = getattr(provider, "ensure_running_async", None)
        model = self.agent.model_name
        if ensure is None or not model:
            return None
        is_serving = getattr(provider, "is_serving", None)
        if is_serving is not None and is_serving():
            return None
        if (
            self._model_start_task is None
            or self._model_start_task.done()
            or self._model_start_for != model
        ):
            self._model_start_for = model
            self._model_start_task = asyncio.create_task(self._run_model_start(provider, ensure))
        return self._model_start_task

    async def _run_model_start(self, provider: Any, ensure: Any) -> None:
        """Start the selected model in a thread (event loop stays responsive)
        and surface progress/errors in the chat."""
        chat = self.query_one("#chat-view", ChatView)
        chat.set_notice(f"starting model {self.agent.model_name}…", spinner=True)
        try:
            await ensure()
            chat.set_notice("model ready")
        except Exception as exc:  # noqa: BLE001 - surface to the user
            chat.dismiss_notice()
            chat.end_assistant_message()
            chat.add_system_message(f"error starting model: {exc}")

    async def _agent_worker(
        self,
        prompt: str,
        attachments: list[ImageAttachment] | None = None,
    ) -> None:
        """Execute agent and handle events."""
        try:
            # A model switch lands here, so the calibration follows the model.
            self._seed_fit_if_model_changed()
            # Wait for the selected model to be serving (non-blocking event loop;
            # the provider launches a detached server and we just poll).
            task = self.start_model()
            if task is not None:
                await task
            await self._renderer.render_agent_run(prompt, self._cancel_event, attachments)
        finally:
            self.remember_latency_fit()
            self.is_processing = False
            self._cancel_event = None
            if self._active_panel is None:
                self.query_one("#prompt-input", PromptInput).focus()

    def seed_latency_fit(self) -> None:
        """Install the persisted wait calibration for the active model."""
        agent = self.agent
        self._fit_seeded_for = agent.model_name
        agent.set_latency_fit(self._bootstrap_config.latency_fit_for(agent.model_name))

    def _seed_fit_if_model_changed(self) -> None:
        """Re-seed only on a switch, so a run's refinement is not thrown away."""
        if self._fit_seeded_for != self.agent.model_name:
            self.seed_latency_fit()

    def remember_latency_fit(self) -> None:
        """Persist the calibration sharpened by this run's measured turns."""
        agent = self.agent
        remember_latency_fit(
            agent.config.session_dir,
            agent.model_name,
            agent.refine_latency_fit(),
        )

    def action_clear(self) -> None:
        """Clear chat history."""
        self._controller.action_clear()

    def action_copy_last_reply(self) -> None:
        """Copy the most recent assistant reply to the clipboard (OSC 52)."""
        chat = self.query_one("#chat-view", ChatView)
        text = chat.last_assistant_text()
        if text is None:
            self.notify("No reply to copy", severity="warning")
            return
        self.copy_to_clipboard(text)
        self.notify("Copied last reply to clipboard")

    async def action_new(self) -> None:
        """Start a new session."""
        await self._controller.action_new()

    def action_escape_pressed(self) -> None:
        """Handle escape key - cancel processing or focus input."""
        if self.is_processing:
            self._cancel_agent()
        else:
            self.query_one("#prompt-input", PromptInput).focus()

    def on_click(self) -> None:
        """Clicking anywhere in the chat puts the caret in the prompt."""
        if self._active_panel is not None:
            return
        self.query_one("#prompt-input", PromptInput).focus()

    def _cancel_agent(self) -> None:
        """Cancel the running agent."""
        if self._cancel_event:
            self._cancel_event.set()  # Signal cancellation to provider

        # Clean up UI and show feedback
        chat = self.query_one("#chat-view", ChatView)
        chat.end_assistant_message()
        chat.add_system_message("interrupted")

    async def on_unmount(self) -> None:
        """Clean up on exit."""
        from marv.instance import clear_record

        clear_record(self._instance_path)
        if self._update_task is not None and not self._update_task.done():
            self._update_task.cancel()
        if self._extension_widget_timer:
            self._extension_widget_timer.stop()
        if self._memory_timer:
            self._memory_timer.stop()
        if self._runtime:
            # Measure the model server before it is unloaded: the sign-off
            # reports the memory that release actually frees, so it has to be
            # read while the weights are still resident. Off-thread, because
            # the footprint read shells out (``ps``/``top``) on the exit path.
            self._exit_summary = ExitSummary(
                session_id=self.agent.session.metadata.id,
                model=self.agent.model_name,
                memory_bytes=await asyncio.to_thread(self._server_memory_bytes),
            )
            await self._runtime.agent.close()

    def _server_memory_bytes(self) -> int:
        """Footprint of the model server right now (0 when none is running)."""
        pid = getattr(self.agent.provider, "server_pid", None)
        if not isinstance(pid, int) or pid <= 0:
            return 0
        return read_process_tree_memory(pid)

    def exit_summary(self) -> ExitSummary:
        """Facts for the CLI's exit sign-off (safe to call before mount runs)."""
        if self._exit_summary is not None:
            return self._exit_summary
        return ExitSummary(session_id=None, model=None, memory_bytes=0)
