"""Main Textual TUI application."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.theme import Theme
from textual.widgets import Static

from marv.tui.chat import ChatView
from marv.tui.compose import TUILoaders, TUIRuntime, build_tui_loaders, build_tui_runtime
from marv.tui.controller import TUIController
from marv.tui.download_modal import DownloadModal
from marv.tui.extension_bridge import TUIExtensionBridge
from marv.tui.input import PromptInput
from marv.tui.memory import snapshot
from marv.tui.model_modal import ModelModal
from marv.tui.renderer import TUIRenderer
from marv.tui.status import StatusBar

if TYPE_CHECKING:
    from textual.timer import Timer

    from marv.config import Config
    from marv.extensions.host import ExtensionHost
    from marv.llm.model_download import DownloadHandle
    from marv.llm.provider import LLMProvider
    from marv.runtime.agent import Agent
    from marv.runtime.session import Session

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


class AgentApp(App[None]):
    """Minimal TUI application for the coding agent."""

    CSS_PATH = "styles.tcss"
    TITLE = "marv"

    BINDINGS = [
        Binding("ctrl+c", "quit", "Quit"),
        Binding("ctrl+l", "clear", "Clear"),
        Binding("escape", "escape_pressed", "Cancel/Focus", show=False),
    ]

    is_processing = reactive(False)

    def __init__(
        self,
        config: Config,
        provider: LLMProvider,
        session: Session | None = None,
    ) -> None:
        super().__init__()
        self._bootstrap_config = config
        self._session = session
        self._provider = provider
        self._runtime: TUIRuntime | None = None
        self._loaders: TUILoaders = build_tui_loaders(config)
        self._controller = TUIController(self)
        self._extension_bridge = TUIExtensionBridge(self)
        self._renderer = TUIRenderer(self, loaders=self._loaders)
        self._spinner_timer: Timer | None = None
        self._extension_widget_timer: Timer | None = None
        self._memory_timer: Timer | None = None

        self._cancel_event: asyncio.Event | None = None
        self._model_start_task: asyncio.Task[None] | None = None
        self._model_start_for: str | None = None

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
        """Animate the spinner in the chat waiting indicator."""
        chat = self.query_one("#chat-view", ChatView)
        chat.advance_waiting()

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

        # Register and apply theme
        self.register_theme(MINIMAL_THEME)
        self.theme = "minimal"

        # Show banner
        self._renderer.render_banner()

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

        # No model is selected/downloaded yet: offer whatever is already in the
        # local hub, or ask which one to fetch when the hub is empty. Either way
        # nothing is downloaded without the user asking for it.
        if not self.agent.model_name:
            if await self._installed_models():
                await self._open_model_modal()
            else:
                await self._open_download_modal()
        elif self._model_is_absent():
            # A model is remembered but is not in the local hub — this is the
            # case that used to trigger an invisible download on startup.
            chat = self.query_one("#chat-view", ChatView)
            chat.add_system_message(f"model {self.agent.model_name} is not downloaded locally")
            await self._open_download_modal(remembered=True)
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

        # Add user message (special handling for $skill-name)
        if not self._renderer.render_skill_invocation(prompt):
            chat.add_user_message(prompt)

        if not self.agent.model_name:
            chat.add_system_message("no model selected — pick one with the picker (or /model <id>)")
            if await self._installed_models():
                await self._open_model_modal()
            else:
                await self._open_download_modal()
            return

        # Start processing - show thinking indicator if thinking is enabled
        self.is_processing = True
        thinking_enabled = (
            self.agent.thinking_level.value != "off" and self.agent.supports_thinking()
        )
        chat.start_assistant_message(thinking=thinking_enabled)

        # Run agent in background
        self._run_agent(prompt)

    def _restore_input_focus(self, _result: object = None) -> None:
        """Focus the prompt input (used as a modal close callback)."""
        self.query_one("#prompt-input", PromptInput).focus()

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
        asyncio.create_task(self._open_download_modal(preselected=model))

    def _create_download(self, model: str) -> DownloadHandle:
        """Create (but do not start) a download for ``model``."""
        provider = self.agent.provider
        factory = getattr(provider, "download_model", None)
        if not callable(factory):
            raise RuntimeError(f"provider {self.agent.provider_name!r} cannot download models")
        return factory(model)  # type: ignore[no-any-return]

    async def _open_download_modal(
        self,
        *,
        remembered: bool = False,
        preselected: str | None = None,
    ) -> None:
        """Open the download picker; the chosen model is applied on dismiss.

        The modal is callback-driven (rather than awaited) so it can be opened
        from ``on_mount`` without stalling startup.
        """
        installed = set(await self._installed_models())

        self.push_screen(
            DownloadModal(
                create_download=self._create_download,
                installed=installed,
                preselected=preselected if preselected is not None else self.agent.model_name,
                remembered=remembered,
            ),
            callback=self._on_download_modal_closed,
        )

    def _on_download_modal_closed(self, model: str | None) -> None:
        """Apply the model chosen in the download picker, then refocus input."""
        if model:
            self._controller.switch_model(model)
        self._restore_input_focus()

    async def _open_model_modal(self) -> None:
        """Open the model picker modal and restore input focus when it closes."""
        models = await self._installed_models()
        self.push_screen(
            ModelModal(self.agent, self._controller.on_model_modal_change, models=models),
            callback=self._restore_input_focus,
        )

    def _run_agent(self, prompt: str) -> None:
        """Run the agent loop."""
        self._cancel_event = asyncio.Event()
        asyncio.create_task(self._agent_worker(prompt))

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
        chat.add_system_message(f"starting model {self.agent.model_name}… (may take a minute)")
        try:
            await ensure()
            chat.add_system_message("model ready")
        except Exception as exc:  # noqa: BLE001 - surface to the user
            chat.end_assistant_message()
            chat.add_system_message(f"error starting model: {exc}")

    async def _agent_worker(self, prompt: str) -> None:
        """Execute agent and handle events."""
        try:
            # Wait for the selected model to be serving (non-blocking event loop;
            # the provider launches a detached server and we just poll).
            task = self.start_model()
            if task is not None:
                await task
            await self._renderer.render_agent_run(prompt, self._cancel_event)
        finally:
            self.is_processing = False
            self._cancel_event = None
            self.query_one("#prompt-input", PromptInput).focus()

    def action_clear(self) -> None:
        """Clear chat history."""
        self._controller.action_clear()

    async def action_new(self) -> None:
        """Start a new session."""
        await self._controller.action_new()

    def action_escape_pressed(self) -> None:
        """Handle escape key - cancel processing or focus input."""
        if self.is_processing:
            self._cancel_agent()
        else:
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
        if self._extension_widget_timer:
            self._extension_widget_timer.stop()
        if self._memory_timer:
            self._memory_timer.stop()
        if self._runtime:
            await self._runtime.agent.close()

    async def push_extension_screen(self, screen: Any) -> object | None:
        """Push a modal screen and await its dismissal without Textual workers."""
        loop = asyncio.get_running_loop()
        future: asyncio.Future[object | None] = loop.create_future()

        def _done(result: object | None) -> None:
            if not future.done():
                future.set_result(result)

        self.push_screen(screen, callback=_done)
        return await future
