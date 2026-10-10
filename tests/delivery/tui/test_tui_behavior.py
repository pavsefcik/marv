"""Behavior tests for TUI flows through the AgentApp runner boundary."""

from __future__ import annotations

import asyncio
import textwrap
from typing import TYPE_CHECKING, Any

import pytest
from textual.widgets import (
    Button,
    Input,
    OptionList,
    ProgressBar,
    Static,
    TextArea,
)
from textual.widgets._toast import Toast

from marv.config import Config
from marv.config.state import LastUsedSelection, load_last_used
from marv.llm import model_download
from marv.llm.events import (
    DoneEvent,
    ErrorEvent,
    PartialMessage,
    StreamOptions,
    TextDeltaEvent,
    TextStartEvent,
    ThinkingDeltaEvent,
)
from marv.llm.model_download import DownloadProgress, hub_model_present
from marv.llm.stream import AssistantMessageEventStream
from marv.runtime.approval import ApprovalMode
from marv.runtime.message import Message, ThinkingContent, ToolCall
from marv.runtime.session import Session
from marv.runtime.settings import InteractionMode, ThinkingLevel
from marv.tui import download_panel
from marv.tui.app import AgentApp
from marv.tui.chat import (
    ChatView,
    MessageWidget,
    SkillInvocationWidget,
    StatusNotice,
    ThinkingWidget,
    ToolWidget,
)
from marv.tui.context_panel import ContextPanel
from marv.tui.download_panel import DownloadPanel
from marv.tui.extension_ui import ConfirmPanel
from marv.tui.input import PromptInput
from marv.tui.model_panel import ModelPanel
from marv.tui.session_panels import SessionForkPanel, SessionLoadPanel, SessionTreePanel
from tests.test_doubles.download_fake import DownloadFake
from tests.test_doubles.llm_provider_fake import LLMProviderFake
from tests.test_doubles.llm_stream_builders import make_text_events, make_tool_call_events

if TYPE_CHECKING:
    from pathlib import Path


def write_skill(dir_path: Path, name: str) -> None:
    skill_dir = dir_path / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        textwrap.dedent(
            f"""
            ---
            name: {name}
            description: test skill
            ---

            # {name}
            """
        ).lstrip()
    )


def write_template(dir_path: Path, filename: str) -> None:
    dir_path.mkdir(parents=True, exist_ok=True)
    (dir_path / filename).write_text(
        textwrap.dedent(
            """
            ---
            name: build
            description: build template
            ---

            run build
            """
        ).lstrip()
    )


def write_extension(path: Path) -> None:
    path.write_text(
        textwrap.dedent(
            """
            from marv.extensions.api import ExtensionAPI

            def setup(api: ExtensionAPI):
                def ping(args, ctx):
                    return "pong"
                api.register_command("ping", ping)
            """
        ).lstrip()
    )


def write_extension_script(path: Path, content: str) -> None:
    path.write_text(textwrap.dedent(content).lstrip())


class CancelAwareProviderFake(LLMProviderFake):
    """Provider double that keeps streaming until cancellation is signaled."""

    def __init__(self) -> None:
        super().__init__([], name="openai", model="gpt-4o")
        self.started = asyncio.Event()

    def stream(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        options: StreamOptions | None = None,
    ) -> AssistantMessageEventStream:
        self.stream_calls.append({"messages": list(messages), "tools": tools, "options": options})

        stream = AssistantMessageEventStream()
        cancel_event = options.cancel_event if options is not None else None

        async def _run() -> None:
            self.started.set()
            stream.push(TextStartEvent(content_index=0))

            while cancel_event is not None and not cancel_event.is_set():
                await asyncio.sleep(0.01)

            if cancel_event is not None and cancel_event.is_set():
                message = PartialMessage(stop_reason="aborted", error_message="cancelled")
                stream.push(ErrorEvent(stop_reason="aborted", message=message))
                stream.end(message)
                return

            stream.push(DoneEvent(message=PartialMessage()))
            stream.end()

        stream.attach_task(asyncio.create_task(_run()))
        return stream


class ServerLifecycleProviderFake(LLMProviderFake):
    """Provider double that records local-server warm-up calls."""

    def __init__(self, *, name: str = "openai", model: str = "gpt-4o") -> None:
        super().__init__([], name=name, model=model)
        self.serving = False
        self.ensure_calls = 0

    def is_serving(self) -> bool:
        return self.serving

    async def ensure_running_async(self) -> bool:
        self.ensure_calls += 1
        self.serving = True
        return True


class HubProviderFake(LLMProviderFake):
    """Provider double with a local model hub and scripted download progress."""

    def __init__(
        self,
        *,
        model: str = "mlx-community/Remembered-4bit",
        hub_dir: Path | None = None,
        installed: list[str] | None = None,
        snapshots: list[DownloadProgress] | None = None,
        download_state: str = "done",
        download_error: str | None = None,
    ) -> None:
        super().__init__([], name="marv-mlx", model=model)
        self.hub_dir = hub_dir
        self.installed = set(installed or [])
        self.downloads: list[DownloadFake] = []
        self._snapshots = list(snapshots or [])
        self._download_state = download_state
        self._download_error = download_error
        self.serving = False
        self.ensure_calls = 0

    async def list_models(self) -> list[str]:
        return sorted(self.installed)

    def is_model_downloaded(self, model: str) -> bool:
        if model in self.installed:
            return True
        return self.hub_dir is not None and hub_model_present(self.hub_dir, model)

    def download_model(self, model: str) -> DownloadFake:
        download = DownloadFake(
            model_id=model,
            snapshots=list(self._snapshots),
            final_state=self._download_state,
            error=self._download_error,
            hub_dir=self.hub_dir,
        )
        self.downloads.append(download)
        return download

    def is_serving(self) -> bool:
        return self.serving

    async def ensure_running_async(self) -> bool:
        self.ensure_calls += 1
        self.serving = True
        return True


def status_left_text(app: AgentApp) -> str:
    status = app.query_one("#status-line")
    left = status.query_one("#status-left", Static)
    renderable = left.render()
    return getattr(renderable, "plain", str(renderable))


def system_messages(app: AgentApp) -> list[str]:
    chat = app.query_one("#chat-view")
    messages: list[str] = []
    for widget in chat.query(Static):
        if "message-system" not in widget.classes:
            continue
        renderable = widget.render()
        messages.append(getattr(renderable, "plain", str(renderable)))
    return messages


def notices(app: AgentApp) -> list[StatusNotice]:
    return list(app.query_one("#chat-view").query(StatusNotice))


def header_text(app: AgentApp) -> str:
    """The startup header line (the only system row naming marv)."""
    for widget in app.query_one("#chat-view").query(Static):
        if "message-system" not in widget.classes:
            continue
        renderable = widget.render()
        text = getattr(renderable, "plain", str(renderable))
        if "marv" in text:
            return text
    return ""


def prompt_input(app: AgentApp) -> TextArea:
    prompt = app.query_one("#prompt-input", PromptInput)
    input_widget = prompt.query_one("#prompt-inner", TextArea)
    input_widget.focus()
    return input_widget


def render_text(widget: Static) -> str:
    renderable = widget.render()
    return getattr(renderable, "plain", str(renderable))


async def submit(app: AgentApp, pilot: Any, text: str) -> None:
    input_widget = prompt_input(app)
    input_widget.text = text
    await pilot.press("enter")
    await pilot.pause()


async def wait_for_idle(app: AgentApp, pilot: Any, ticks: int = 50) -> None:
    for _ in range(ticks):
        if not app.is_processing:
            return
        await pilot.pause()
    assert not app.is_processing


@pytest.mark.asyncio
async def test_tui_runner_autocomplete_includes_skills_templates_and_extensions(temp_dir):
    skills_dir = temp_dir / "skills"
    templates_dir = temp_dir / "templates"
    ext_path = temp_dir / "ext_ping.py"

    write_skill(skills_dir, "deploy")
    write_template(templates_dir, "build.md")
    write_extension(ext_path)

    config = Config(
        provider="openai",
        model="gpt-4o",
        api_key="test",
        session_dir=temp_dir,
        skills_dirs=[skills_dir],
        prompt_template_dirs=[templates_dir],
        extensions=[ext_path],
    )
    app = AgentApp(config, provider=LLMProviderFake([]))

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()

        input_widget = prompt_input(app)

        await pilot.press("$")
        await pilot.pause()

        prompt = app.query_one("#prompt-input", PromptInput)
        option_list = prompt.query_one("#suggestions", OptionList)
        options = [
            option_list.get_option_at_index(i).prompt for i in range(option_list.option_count)
        ]
        assert "$deploy" in options

        input_widget.text = ""
        await pilot.press("/")
        await pilot.pause()

        options = [
            option_list.get_option_at_index(i).prompt for i in range(option_list.option_count)
        ]
        assert "/build" in options
        assert "/ping" in options


@pytest.mark.asyncio
async def test_tui_extension_input_prompt_round_trip(temp_dir):
    ext_path = temp_dir / "ext_prompt.py"
    write_extension_script(
        ext_path,
        """
        from marv.extensions.api import ExtensionAPI

        def setup(api: ExtensionAPI):
            async def ask(args, ctx):
                if ctx.ui is None:
                    return "no-ui"
                name = await ctx.ui.input("What is your name?", default="anon")
                return f"hello {name}"
            api.register_command("ask-name", ask)
        """,
    )

    config = Config(
        provider="openai",
        model="gpt-4o",
        api_key="test",
        session_dir=temp_dir,
        extensions=[ext_path],
    )
    app = AgentApp(config, provider=LLMProviderFake([]))

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        await submit(app, pilot, "/ask-name ")
        await pilot.pause()

        modal_input = app.query_one("#extension-prompt-input", Input)
        modal_input.focus()
        modal_input.value = "bob"
        await pilot.press("enter")
        await wait_for_idle(app, pilot)

        assert any("hello bob" in message for message in system_messages(app))


@pytest.mark.asyncio
async def test_tui_presented_read_only_view_shows_close_button_without_input(temp_dir):
    ext_path = temp_dir / "ext_presented_readonly.py"
    write_extension_script(
        ext_path,
        """
        from marv.extensions.api import ExtensionAPI, ViewControl

        class ReadOnlyView:
            def __init__(self):
                self._closed = False

            def render(self):
                return "read only details"

            def controls(self):
                return [ViewControl(kind="button", name="close", label="Close", primary=True)]

            def handle_action(self, action, value=None):
                if action == "close":
                    self._closed = True

            def is_done(self):
                return self._closed

            def result(self):
                return "closed"

        def setup(api: ExtensionAPI):
            async def show(args, ctx):
                if ctx.ui is None:
                    return "no-ui"
                result = await ctx.ui.present(ReadOnlyView())
                return f"result:{result}"
            api.register_command("show-readonly", show)
        """,
    )

    config = Config(
        provider="openai",
        model="gpt-4o",
        api_key="test",
        session_dir=temp_dir,
        extensions=[ext_path],
    )
    app = AgentApp(config, provider=LLMProviderFake([]))

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        await submit(app, pilot, "/show-readonly ")
        await pilot.pause()

        presented_content = app.query_one("#extension-presented-content", Static)
        assert "read only details" in render_text(presented_content)
        buttons = app.query(Button)
        assert any(button.id == "extension-presented-button-close" for button in buttons)
        assert not list(app.query("#extension-presented-input-command"))

        await pilot.press("enter")
        await wait_for_idle(app, pilot)

        assert any("result:closed" in message for message in system_messages(app))


@pytest.mark.asyncio
async def test_tui_extension_widget_renders_in_footer_and_right_panel(temp_dir):
    ext_path = temp_dir / "ext_widget.py"
    write_extension_script(
        ext_path,
        """
        from marv.extensions.api import ExtensionAPI

        class DemoWidget:
            def __init__(self, text):
                self.text = text

            def render(self):
                return self.text

        def setup(api: ExtensionAPI):
            def widgets(args, ctx):
                if ctx.ui is None:
                    return "no-ui"
                ctx.ui.set_widget("footer", DemoWidget("footer text"))
                ctx.ui.set_widget("right_panel", DemoWidget("right text"))
                return "ok"
            api.register_command("widgets", widgets)
        """,
    )

    config = Config(
        provider="openai",
        model="gpt-4o",
        api_key="test",
        session_dir=temp_dir,
        extensions=[ext_path],
    )
    app = AgentApp(config, provider=LLMProviderFake([]))

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        await submit(app, pilot, "/widgets ")
        await wait_for_idle(app, pilot)

        footer = app.query_one("#extension-footer", Static)
        panel = app.query_one("#extension-right-panel", Static)
        assert "footer text" in render_text(footer)
        assert "right text" in render_text(panel)


@pytest.mark.asyncio
async def test_tui_runner_autocomplete_tab_and_history_navigation(temp_dir):
    config = Config(provider="openai", model="gpt-4o", api_key="test", session_dir=temp_dir)
    app = AgentApp(config, provider=LLMProviderFake([]))

    async with app.run_test() as pilot:
        await pilot.pause()

        input_widget = prompt_input(app)
        await pilot.press("/", "h")
        await pilot.pause()

        prompt = app.query_one("#prompt-input", PromptInput)
        option_list = prompt.query_one("#suggestions", OptionList)
        assert option_list.display

        await pilot.press("tab")
        await pilot.pause()
        assert input_widget.text == "/help"

        input_widget.text = "/help "
        await pilot.press("enter")
        await pilot.pause()
        assert any("ctrl+c quit" in msg for msg in system_messages(app))

        await submit(app, pilot, "first prompt")
        await wait_for_idle(app, pilot)

        await submit(app, pilot, "second prompt")
        await wait_for_idle(app, pilot)

        input_widget = prompt_input(app)
        input_widget.text = ""
        await pilot.press("up")
        await pilot.pause()
        assert input_widget.text == "second prompt"

        await pilot.press("up")
        await pilot.pause()
        assert input_widget.text == "first prompt"

        await pilot.press("down")
        await pilot.pause()
        assert input_widget.text == "second prompt"

        await pilot.press("down")
        await pilot.pause()
        assert input_widget.text == ""


@pytest.mark.asyncio
async def test_tui_runner_model_menu_updates_model_and_thinking(temp_dir):
    session = Session.new(temp_dir)
    config = Config(
        provider="openai",
        model="gpt-4o",
        api_key="test",
        session_dir=temp_dir,
        thinking_level=ThinkingLevel.OFF,
    )
    provider = LLMProviderFake(
        [],
        name="openai",
        model="gpt-4o",
        available_models=["gpt-4o", "gpt-5"],
    )
    app = AgentApp(config, provider=provider, session=session)

    async with app.run_test() as pilot:
        await pilot.pause()

        prompt = app.query_one("#prompt-input", PromptInput)
        input_widget = prompt.query_one("#prompt-inner", TextArea)
        input_widget.focus()
        # Trailing space avoids autocomplete interception on first Enter.
        input_widget.text = "/model "
        await pilot.press("enter")
        await pilot.pause()

        panel = app.active_panel
        assert isinstance(panel, ModelPanel)

        # Picking a model applies it and returns to input mode.
        options = panel.query_one("#panel-options", OptionList)
        ids = [str(options.get_option_at_index(i).id) for i in range(options.option_count)]
        options.highlighted = ids.index("gpt-5")
        options.action_select()
        await pilot.pause()

        assert app.active_panel is None
        assert app.agent.provider.model == "gpt-5"
        assert "gpt-5" in status_left_text(app)

        # Reopen to change thinking through the thinking submenu.
        input_widget.text = "/model "
        await pilot.press("enter")
        await pilot.pause()
        panel = app.active_panel
        assert isinstance(panel, ModelPanel)

        options = panel.query_one("#panel-options", OptionList)
        options.highlighted = options.option_count - 1  # the thinking entry
        options.action_select()
        await pilot.pause()

        options = panel.query_one("#panel-options", OptionList)
        ids = [str(options.get_option_at_index(i).id) for i in range(options.option_count)]
        low_index = ids.index(ThinkingLevel.LOW.value)
        assert not options.get_option_at_index(low_index).disabled
        options.highlighted = low_index
        options.action_select()
        await pilot.pause()

        assert app.active_panel is None
        assert app.agent.provider.model == "gpt-5"
        assert app.agent.config.thinking_level == ThinkingLevel.LOW
        left = status_left_text(app)
        assert "gpt-5" in left
        assert "thinking:low" in left


@pytest.mark.asyncio
async def test_tui_runner_model_command_switches_and_rejects_invalid_model(temp_dir):
    config = Config(
        provider="openai-codex", model="gpt-5-codex", api_key="test", session_dir=temp_dir
    )
    provider = LLMProviderFake(
        [],
        name="openai-codex",
        model="gpt-5-codex",
        available_models=["gpt-5-codex", "gpt-5"],
    )
    app = AgentApp(config, provider=provider)

    async with app.run_test() as pilot:
        await pilot.pause()

        await submit(app, pilot, "/model gpt-5")
        assert app.agent.provider.model == "gpt-5"
        assert any("switched to gpt-5" in msg for msg in system_messages(app))
        assert "gpt-5" in status_left_text(app)

        await submit(app, pilot, "/model gpt-4o")
        assert app.agent.provider.model == "gpt-5"
        assert any("not valid for provider 'openai-codex'" in msg for msg in system_messages(app))
        assert "gpt-5" in status_left_text(app)


@pytest.mark.asyncio
async def test_tui_runner_model_switch_updates_the_header_and_replaces_the_notice(temp_dir):
    """The header follows a live switch and the status is one replacing row."""
    config = Config(
        provider="openai-codex", model="gpt-5-codex", api_key="test", session_dir=temp_dir
    )
    provider = LLMProviderFake(
        [],
        name="openai-codex",
        model="gpt-5-codex",
        available_models=["gpt-5-codex", "gpt-5"],
    )
    app = AgentApp(config, provider=provider)

    async with app.run_test() as pilot:
        await pilot.pause()
        assert "gpt-5-codex" in header_text(app)

        await submit(app, pilot, "/model gpt-5")

        # The header shows the model now in use, not the one we started with.
        assert "gpt-5" in header_text(app)
        assert "gpt-5-codex" not in header_text(app)
        # One status row, replaced in place rather than appended to a log.
        assert [n.text_content() for n in notices(app)] == ["switched to gpt-5"]

        await submit(app, pilot, "/model gpt-5-codex")
        assert [n.text_content() for n in notices(app)] == ["switched to gpt-5-codex"]


class SlowStartProviderFake(ServerLifecycleProviderFake):
    """A local server that stays in warm-up until the test releases it."""

    def __init__(self) -> None:
        super().__init__()
        self.release = asyncio.Event()

    async def ensure_running_async(self) -> bool:
        self.ensure_calls += 1
        await self.release.wait()
        self.serving = True
        return True


@pytest.mark.asyncio
async def test_tui_runner_model_start_is_one_spinning_notice_then_ready(temp_dir):
    """Warm-up is a single animated row that becomes ``model ready`` in place."""
    config = Config(
        provider="openai",
        model="gpt-4o",
        api_key="test",
        session_dir=temp_dir / "sessions",
    )
    provider = SlowStartProviderFake()
    app = AgentApp(config, provider=provider)

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()

        starting = notices(app)
        assert [n.text_content() for n in starting] == ["starting model gpt-4o…"]
        assert starting[0].spinning

        provider.release.set()
        await pilot.pause()
        await pilot.pause()

        ready = notices(app)
        assert [n.text_content() for n in ready] == ["model ready"]
        assert not ready[0].spinning


class FailingStartProviderFake(ServerLifecycleProviderFake):
    """A local server whose warm-up fails."""

    async def ensure_running_async(self) -> bool:
        self.ensure_calls += 1
        raise RuntimeError("boom")


@pytest.mark.asyncio
async def test_tui_runner_failed_model_start_dismisses_the_spinner(temp_dir):
    """A failed warm-up stops spinning and reports the error permanently."""
    config = Config(
        provider="openai",
        model="gpt-4o",
        api_key="test",
        session_dir=temp_dir / "sessions",
    )
    app = AgentApp(config, provider=FailingStartProviderFake())

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()

        assert notices(app) == []
        assert any("error starting model: boom" in m for m in system_messages(app))


@pytest.mark.asyncio
async def test_tui_runner_rehydrates_tool_and_thinking_widgets(temp_dir):
    session = Session.new(temp_dir)

    tool_call = ToolCall(id="call_1", name="read", arguments={"path": "file.txt"})
    session.append(Message.user("Read"))
    session.append(
        Message.assistant(
            "Here you go",
            tool_calls=[tool_call],
            thinking=ThinkingContent(text="thinking..."),
        )
    )
    session.append(Message.tool_result("call_1", "result"))

    config = Config(
        provider="openai",
        model="gpt-4o",
        api_key="test",
        session_dir=temp_dir,
        context_max_tokens=2048,
        max_output_tokens=2048,
    )

    app = AgentApp(
        config,
        provider=LLMProviderFake([], name="openai", model="gpt-4o"),
        session=session,
    )
    async with app.run_test() as pilot:
        await pilot.pause()

        chat = app.query_one("#chat-view")
        tool_widgets = list(chat.query(ToolWidget))
        thinking_widgets = list(chat.query(ThinkingWidget))
        assistant_widgets = list(chat.query(MessageWidget))

        assert tool_widgets
        assert thinking_widgets
        assert any("Here you go" in w.text_content() for w in assistant_widgets)

        thinking_widget = thinking_widgets[0]
        thinking_widget.toggle()
        await pilot.pause()
        assert "thinking-collapsed" in thinking_widget.classes
        assert "click to expand" in render_text(thinking_widget)

        thinking_widget.toggle()
        await pilot.pause()
        assert "thinking-collapsed" not in thinking_widget.classes


@pytest.mark.asyncio
async def test_tui_runner_keeps_thinking_above_assistant_when_thinking_arrives_late(temp_dir):
    config = Config(provider="openai", model="gpt-4o", api_key="test", session_dir=temp_dir)
    provider = LLMProviderFake(
        [
            [
                TextStartEvent(content_index=0),
                TextDeltaEvent(content_index=0, delta="Answer first."),
                ThinkingDeltaEvent(content_index=1, delta="Late reasoning."),
                DoneEvent(message=PartialMessage()),
            ]
        ],
        name="openai",
        model="gpt-4o",
    )
    app = AgentApp(config, provider=provider)

    async with app.run_test() as pilot:
        await pilot.pause()

        await submit(app, pilot, "trigger late thinking")
        await wait_for_idle(app, pilot)

        chat = app.query_one("#chat-view")
        children = list(chat.children)
        thinking_widgets = list(chat.query(ThinkingWidget))
        assistant_widgets = [w for w in chat.query(MessageWidget) if w.role == "assistant"]

        assert thinking_widgets
        assert assistant_widgets
        assert children.index(thinking_widgets[-1]) < children.index(assistant_widgets[-1])


@pytest.mark.asyncio
async def test_tui_runner_auto_collapses_previous_thinking_on_new_activity(temp_dir):
    config = Config(provider="openai", model="gpt-4o", api_key="test", session_dir=temp_dir)
    provider = LLMProviderFake(
        [
            [
                ThinkingDeltaEvent(content_index=0, delta="First reasoning block."),
                TextStartEvent(content_index=1),
                TextDeltaEvent(content_index=1, delta="Answer text."),
                ThinkingDeltaEvent(content_index=2, delta="Second reasoning block."),
                DoneEvent(message=PartialMessage()),
            ]
        ],
        name="openai",
        model="gpt-4o",
    )
    app = AgentApp(config, provider=provider)

    async with app.run_test() as pilot:
        await pilot.pause()

        await submit(app, pilot, "trigger multi thinking")
        await wait_for_idle(app, pilot)

        chat = app.query_one("#chat-view")
        thinking_widgets = list(chat.query(ThinkingWidget))

        assert len(thinking_widgets) == 2
        assert "thinking-collapsed" in thinking_widgets[0].classes
        assert "thinking-collapsed" not in thinking_widgets[1].classes


@pytest.mark.asyncio
async def test_tui_runner_session_commands_report_errors_for_bad_targets(temp_dir):
    missing_session_file = temp_dir / "missing.jsonl"

    config = Config(provider="openai", model="gpt-4o", api_key="test", session_dir=temp_dir)
    app = AgentApp(config, provider=LLMProviderFake([]))

    async with app.run_test() as pilot:
        await pilot.pause()

        await submit(app, pilot, f"/load {missing_session_file}")
        assert any("session file not found:" in msg for msg in system_messages(app))

        await submit(app, pilot, "/fork not-a-message")
        assert any(
            "could not resolve message: not-a-message" in msg for msg in system_messages(app)
        )

        await submit(app, pilot, "/tree not-an-entry")
        assert any("could not resolve entry: not-an-entry" in msg for msg in system_messages(app))


@pytest.mark.asyncio
async def test_tui_runner_help_new_and_clear_commands_update_runtime_state(temp_dir):
    config = Config(provider="openai", model="gpt-4o", api_key="test", session_dir=temp_dir)
    app = AgentApp(config, provider=LLMProviderFake([]))

    async with app.run_test() as pilot:
        await pilot.pause()

        initial_session_id = app.agent.session.metadata.id

        await submit(app, pilot, "/help ")
        assert any("ctrl+c quit" in msg for msg in system_messages(app))

        await submit(app, pilot, "/new ")
        new_session_id = app.agent.session.metadata.id
        assert new_session_id != initial_session_id
        assert any("new session started" in msg for msg in system_messages(app))
        assert f"session:{new_session_id}" in status_left_text(app)

        await submit(app, pilot, "hello")
        await wait_for_idle(app, pilot)

        await submit(app, pilot, "/clear ")
        assert any("cleared" in msg for msg in system_messages(app))

        chat = app.query_one("#chat-view")
        user_messages = [w for w in chat.query(MessageWidget) if w.role == "user"]
        assert not user_messages


@pytest.mark.asyncio
async def test_tui_runner_chat_and_tools_commands_toggle_mode(temp_dir):
    config = Config(provider="openai", model="gpt-4o", api_key="test", session_dir=temp_dir)
    app = AgentApp(config, provider=LLMProviderFake([]))

    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.agent.interaction_mode is InteractionMode.TOOLS

        await submit(app, pilot, "/chat ")
        assert app.agent.interaction_mode is InteractionMode.CHAT
        assert "[chat]" in status_left_text(app)
        assert any("chat mode" in msg for msg in system_messages(app))

        # Mode is agent state, so a new session keeps the last choice.
        await submit(app, pilot, "/new ")
        assert app.agent.interaction_mode is InteractionMode.CHAT
        assert "[chat]" in status_left_text(app)

        await submit(app, pilot, "/tools ")
        assert app.agent.interaction_mode is InteractionMode.TOOLS
        assert "[chat]" not in status_left_text(app)
        assert any("tool mode" in msg for msg in system_messages(app))


@pytest.mark.asyncio
async def test_tui_runner_escape_interrupts_active_response(temp_dir):
    config = Config(provider="openai", model="gpt-4o", api_key="test", session_dir=temp_dir)
    provider = CancelAwareProviderFake()
    app = AgentApp(config, provider=provider)

    async with app.run_test() as pilot:
        await pilot.pause()

        await submit(app, pilot, "interrupt me")

        for _ in range(20):
            if app.is_processing and provider.started.is_set():
                break
            await pilot.pause()
        assert app.is_processing

        await pilot.press("escape")
        await pilot.pause()
        await wait_for_idle(app, pilot)

        assert any("interrupted" in msg for msg in system_messages(app))
        assert not app.is_processing


@pytest.mark.asyncio
async def test_tui_runner_skill_command_renders_skill_block_and_args(temp_dir):
    skills_dir = temp_dir / "skills"
    write_skill(skills_dir, "deploy")

    config = Config(
        provider="openai",
        model="gpt-4o",
        api_key="test",
        session_dir=temp_dir,
        skills_dirs=[skills_dir],
    )
    app = AgentApp(config, provider=LLMProviderFake([]))

    async with app.run_test() as pilot:
        await pilot.pause()

        await submit(app, pilot, "$deploy --prod")
        await wait_for_idle(app, pilot)

        chat = app.query_one("#chat-view")
        skill_widgets = list(chat.query(SkillInvocationWidget))
        user_messages = [w for w in chat.query(MessageWidget) if w.role == "user"]

        assert skill_widgets
        assert skill_widgets[0].skill_name == "deploy"
        assert any(w.text_content() == "--prod" for w in user_messages)


@pytest.mark.asyncio
async def test_tui_runner_status_displays_model_thinking_and_session(temp_dir):
    session = Session.new(temp_dir)
    config = Config(
        provider="openai",
        model="gpt-4o",
        api_key="test",
        session_dir=temp_dir,
        thinking_level=ThinkingLevel.LOW,
    )
    app = AgentApp(
        config,
        provider=LLMProviderFake([], name="openai", model="gpt-4o"),
        session=session,
    )

    async with app.run_test() as pilot:
        await pilot.pause()

        left = status_left_text(app)
        assert "gpt-4o" in left
        assert "thinking:low" in left
        assert f"session:{session.metadata.id}" in left


@pytest.mark.asyncio
async def test_tui_runner_context_menu_shows_context_and_closes(temp_dir):
    skills_dir = temp_dir / "skills"
    templates_dir = temp_dir / "templates"
    write_skill(skills_dir, "deploy")
    write_template(templates_dir, "build.md")

    session = Session.new(temp_dir)
    session.append(Message.user("hello"))
    session.append(Message.assistant("world"))

    config = Config(
        provider="openai",
        model="gpt-4o",
        api_key="test",
        session_dir=temp_dir,
        skills_dirs=[skills_dir],
        prompt_template_dirs=[templates_dir],
    )
    app = AgentApp(
        config,
        provider=LLMProviderFake([], name="openai", model="gpt-4o"),
        session=session,
    )

    async with app.run_test() as pilot:
        await pilot.pause()

        await submit(app, pilot, "/context ")
        panel = app.active_panel
        assert isinstance(panel, ContextPanel)

        summary_text = render_text(panel.query_one("#summary-content", Static))
        assert "Provider: openai" in summary_text
        assert "Model: gpt-4o" in summary_text
        assert "MESSAGES" in summary_text
        assert "SKILLS" in summary_text
        assert "TEMPLATES" in summary_text

        # Tabs swap the visible section.
        assert not panel.query_one("#messages-content", Static).display
        await pilot.press("right")
        await pilot.pause()
        assert panel.query_one("#messages-content", Static).display
        messages_text = render_text(panel.query_one("#messages-content", Static))
        assert "USER" in messages_text
        assert "ASSISTANT" in messages_text
        assert "Total:" in messages_text

        await pilot.press("right")
        await pilot.pause()
        assert panel.query_one("#system-content", Static).display
        system_text = render_text(panel.query_one("#system-content", Static))
        assert "Total:" in system_text or "(no system prompt)" in system_text

        await pilot.press("escape")
        await pilot.pause()
        assert app.active_panel is None


@pytest.mark.asyncio
async def test_tui_runner_load_menu_selects_session_and_returns_to_input(temp_dir):
    target = Session.new(temp_dir)
    target.append(Message.user("target-session"))

    current = Session.new(temp_dir)
    current.append(Message.user("current-session"))

    config = Config(provider="openai", model="gpt-4o", api_key="test", session_dir=temp_dir)
    app = AgentApp(config, provider=LLMProviderFake([]), session=current)

    async with app.run_test() as pilot:
        await pilot.pause()

        await submit(app, pilot, "/load ")
        panel = app.active_panel
        assert isinstance(panel, SessionLoadPanel)

        options = panel.query_one("#panel-options", OptionList)
        selected_path = str(options.get_option_at_index(options.highlighted).id)
        assert selected_path is not None

        options.action_select()
        await pilot.pause()

        assert str(app.agent.session.path) == selected_path
        assert any("loaded session" in msg for msg in system_messages(app))
        assert app.active_panel is None

        # Choosing drops straight back into input mode.
        assert app.query_one("#prompt-input", PromptInput).display
        assert isinstance(app.focused, TextArea)


@pytest.mark.asyncio
async def test_tui_runner_fork_menu_forks_from_selected_message(temp_dir):
    session = Session.new(temp_dir)
    session.append(Message.user("first"))
    session.append(Message.assistant("second"))
    session.append(Message.user("third"))

    parent_session_id = session.metadata.id
    config = Config(provider="openai", model="gpt-4o", api_key="test", session_dir=temp_dir)
    app = AgentApp(config, provider=LLMProviderFake([]), session=session)

    async with app.run_test() as pilot:
        await pilot.pause()

        await submit(app, pilot, "/fork ")
        panel = app.active_panel
        assert isinstance(panel, SessionForkPanel)

        options = panel.query_one("#panel-options", OptionList)
        options.highlighted = 0
        selected_message_id = str(options.get_option_at_index(0).id)
        assert selected_message_id is not None

        options.action_select()
        await pilot.pause()

        assert app.agent.session.metadata.parent_session_id == parent_session_id
        assert len(app.agent.session.messages) == 1
        assert any(
            f"forked from {parent_session_id} at {selected_message_id}" in msg
            for msg in system_messages(app)
        )
        assert app.active_panel is None


@pytest.mark.asyncio
async def test_tui_runner_tree_menu_updates_leaf(temp_dir):
    session = Session.new(temp_dir)
    session.append(Message.user("first"))
    session.append(Message.assistant("second"))
    session.append(Message.user("third"))

    config = Config(provider="openai", model="gpt-4o", api_key="test", session_dir=temp_dir)
    app = AgentApp(config, provider=LLMProviderFake([]), session=session)

    async with app.run_test() as pilot:
        await pilot.pause()

        await submit(app, pilot, "/tree ")
        panel = app.active_panel
        assert isinstance(panel, SessionTreePanel)

        options = panel.query_one("#panel-options", OptionList)
        options.highlighted = 1
        selected_entry_id = str(options.get_option_at_index(1).id)
        assert selected_entry_id is not None

        options.action_select()
        await pilot.pause()

        assert app.agent.session.leaf_id == selected_entry_id
        assert len(app.agent.session.messages) == 2
        assert any(f"branched to {selected_entry_id}" in msg for msg in system_messages(app))
        assert app.active_panel is None


@pytest.mark.asyncio
async def test_tui_runner_tree_menu_branch_updates_leaf(temp_dir):
    session = Session.new(temp_dir)
    first = Message.user("first")
    second = Message.assistant("second")
    third = Message.user("third")
    branch = Message.assistant("branch")
    session.append(first)
    session.append(second)
    session.append(third)
    session.set_leaf(second.id)
    session.append(branch)

    current_leaf = session.leaf_id
    config = Config(provider="openai", model="gpt-4o", api_key="test", session_dir=temp_dir)
    app = AgentApp(config, provider=LLMProviderFake([]), session=session)

    async with app.run_test() as pilot:
        await pilot.pause()

        await submit(app, pilot, "/tree ")
        panel = app.active_panel
        assert isinstance(panel, SessionTreePanel)

        options = panel.query_one("#panel-options", OptionList)
        count = options.option_count
        assert count >= 2
        options.highlighted = count - 2  # a sibling branch, not the current leaf
        target_entry_id = str(options.get_option_at_index(count - 2).id)
        assert target_entry_id != current_leaf

        options.action_select()
        await pilot.pause()

        assert app.agent.session.leaf_id == target_entry_id
        assert any(f"branched to {target_entry_id}" in msg for msg in system_messages(app))
        assert app.active_panel is None


@pytest.mark.asyncio
async def test_tui_runner_switching_model_persists_last_used_selection(temp_dir):
    config = Config(
        provider="openai",
        model="gpt-4o",
        api_key="test",
        session_dir=temp_dir / "sessions",
    )
    provider = LLMProviderFake(
        [],
        name="openai",
        model="gpt-4o",
        available_models=["gpt-4o", "gpt-5"],
    )
    app = AgentApp(config, provider=provider)

    async with app.run_test() as pilot:
        await pilot.pause()

        await submit(app, pilot, "/model gpt-5")
        await pilot.pause()

        remembered = load_last_used(temp_dir / "sessions")
        assert remembered.provider == "openai"
        assert remembered.model == "gpt-5"


@pytest.mark.asyncio
async def test_tui_runner_warms_model_server_on_startup(temp_dir):
    config = Config(
        provider="openai",
        model="gpt-4o",
        api_key="test",
        session_dir=temp_dir / "sessions",
    )
    provider = ServerLifecycleProviderFake()
    app = AgentApp(config, provider=provider)

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()

        assert provider.ensure_calls == 1
        assert any("model ready" in msg for msg in system_messages(app))


@pytest.mark.asyncio
async def test_tui_runner_does_not_start_server_when_model_missing(temp_dir):
    config = Config(
        provider="openai",
        model="",
        api_key="test",
        session_dir=temp_dir / "sessions",
    )
    provider = ServerLifecycleProviderFake()
    provider.model = ""
    app = AgentApp(config, provider=provider)

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()

        assert provider.ensure_calls == 0


@pytest.mark.asyncio
async def test_tui_runner_remembers_thinking_level(temp_dir):
    config = Config(
        provider="openai",
        model="gpt-5",
        api_key="test",
        session_dir=temp_dir / "sessions",
        thinking_level=ThinkingLevel.OFF,
    )
    provider = LLMProviderFake([], name="openai", model="gpt-5")
    app = AgentApp(config, provider=provider)

    async with app.run_test() as pilot:
        await pilot.pause()

        app._controller.switch_thinking("low")
        await pilot.pause()

        remembered = load_last_used(temp_dir / "sessions")
        assert remembered.model == "gpt-5"
        assert remembered.thinking_level == "low"


@pytest.mark.asyncio
async def test_tui_runner_start_with_remembered_model_skips_picker(temp_dir, monkeypatch):
    home = temp_dir / "home"
    project = temp_dir / "project"
    home.mkdir()
    project.mkdir()

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(project)

    LastUsedSelection(provider="openai", model="gpt-5", thinking_level="low").save(
        home / ".cache" / "marv" / "sessions"
    )

    config = Config.load()
    provider = LLMProviderFake([], name=config.provider, model=config.model)
    app = AgentApp(config, provider=provider)

    async with app.run_test() as pilot:
        await pilot.pause()

        assert config.model == "gpt-5"
        assert app.agent.model_name == "gpt-5"
        assert app.active_panel is None
        assert "gpt-5" in status_left_text(app)


@pytest.mark.asyncio
async def test_tui_runner_confirms_before_destructive_tool(temp_dir):
    target = temp_dir / "note.txt"
    scripts = [
        make_tool_call_events("c1", "write", {"path": str(target), "content": "hi"}),
        make_text_events("done"),
    ]
    config = Config(
        provider="openai",
        model="gpt-4o",
        api_key="test",
        session_dir=temp_dir / "sessions",
        approval_mode=ApprovalMode.DESTRUCTIVE,
    )
    provider = LLMProviderFake(scripts, name="openai", model="gpt-4o")
    app = AgentApp(config, provider=provider)

    async with app.run_test() as pilot:
        await pilot.pause()
        await submit(app, pilot, "write it")
        for _ in range(20):
            if isinstance(app.active_panel, ConfirmPanel):
                break
            await pilot.pause()

        assert isinstance(app.active_panel, ConfirmPanel)
        await pilot.press("y")
        await pilot.pause()
        await wait_for_idle(app, pilot)

    assert target.read_text() == "hi"


@pytest.mark.asyncio
async def test_tui_runner_denies_destructive_tool_on_reject(temp_dir):
    target = temp_dir / "note.txt"
    scripts = [
        make_tool_call_events("c1", "write", {"path": str(target), "content": "hi"}),
        make_text_events("done"),
    ]
    config = Config(
        provider="openai",
        model="gpt-4o",
        api_key="test",
        session_dir=temp_dir / "sessions",
        approval_mode=ApprovalMode.DESTRUCTIVE,
    )
    provider = LLMProviderFake(scripts, name="openai", model="gpt-4o")
    app = AgentApp(config, provider=provider)

    async with app.run_test() as pilot:
        await pilot.pause()
        await submit(app, pilot, "write it")
        for _ in range(20):
            if isinstance(app.active_panel, ConfirmPanel):
                break
            await pilot.pause()

        assert isinstance(app.active_panel, ConfirmPanel)
        await pilot.press("n")
        await pilot.pause()
        await wait_for_idle(app, pilot)

    assert not target.exists()


def make_hub_config(temp_dir: Path, model: str = "") -> Config:
    return Config(
        provider="marv-mlx",
        model=model,
        api_key="",
        session_dir=temp_dir / "sessions",
    )


@pytest.mark.asyncio
async def test_tui_runner_offers_the_download_picker_when_no_model_is_available(temp_dir):
    provider = HubProviderFake(model="", hub_dir=temp_dir, installed=[])
    app = AgentApp(make_hub_config(temp_dir, model=""), provider=provider)

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()

        panel = app.active_panel
        assert isinstance(panel, DownloadPanel)
        options = panel.query_one("#download-options", OptionList)
        assert options.option_count > 0


@pytest.mark.asyncio
async def test_download_picker_shows_the_runtime_catalog_id_and_tagline(
    temp_dir: Path, monkeypatch
):
    catalog = temp_dir / "curated-llms.md"
    catalog.write_text(
        "16 GB RAM Tier Models\n\nornith-ai/Ornith-1.5-9B-MLX-4bit\nThe coding sniper\n"
    )
    monkeypatch.setattr(model_download, "DEFAULT_CATALOG_PATH", catalog)
    monkeypatch.setattr(model_download, "installed_ram_bytes", lambda: 16 * 1024**3)

    app = AgentApp(
        make_hub_config(temp_dir, model=""),
        provider=HubProviderFake(model="", hub_dir=temp_dir, installed=[]),
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()

        panel = app.active_panel
        assert isinstance(panel, DownloadPanel)
        options = panel.query_one("#download-options", OptionList)
        rendered = [str(options.get_option_at_index(i).prompt) for i in range(options.option_count)]
        assert any("ornith-ai/Ornith-1.5-9B-MLX-4bit" in entry for entry in rendered)
        assert any("The coding sniper" in entry for entry in rendered)
        assert not any("t3, vision" in entry for entry in rendered)


@pytest.mark.asyncio
async def test_download_picker_waits_for_the_in_flight_catalog_refresh(temp_dir: Path, monkeypatch):
    """The first picker of a session reflects the just-completed refresh."""
    catalog = temp_dir / "curated-llms.md"
    catalog.write_text("16 GB RAM Tier Models\n\nmlx-community/Stale-4bit\nThe old one\n")
    monkeypatch.setattr(model_download, "DEFAULT_CATALOG_PATH", catalog)
    monkeypatch.setattr(model_download, "installed_ram_bytes", lambda: 16 * 1024**3)

    def rewrite_then_finish() -> bool:
        # Stand in for `marv-mlx curated` finishing: the cache is now the new list.
        catalog.write_text(
            "16 GB RAM Tier Models\n\nornith-ai/Ornith-1.5-9B-MLX-4bit\nThe coding sniper\n"
        )
        return True

    monkeypatch.setattr(download_panel, "wait_for_catalog_refresh", lambda: rewrite_then_finish())

    app = AgentApp(
        make_hub_config(temp_dir, model=""),
        provider=HubProviderFake(model="", hub_dir=temp_dir, installed=[]),
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()

        panel = app.active_panel
        assert isinstance(panel, DownloadPanel)
        options = panel.query_one("#download-options", OptionList)
        rendered = [str(options.get_option_at_index(i).prompt) for i in range(options.option_count)]
        assert any("ornith-ai/Ornith-1.5-9B-MLX-4bit" in entry for entry in rendered)
        assert not any("Stale-4bit" in entry for entry in rendered)


@pytest.mark.asyncio
async def test_tui_runner_asks_before_downloading_a_missing_remembered_model(temp_dir):
    provider = HubProviderFake(
        model="mlx-community/Remembered-4bit", hub_dir=temp_dir, installed=[]
    )
    app = AgentApp(
        make_hub_config(temp_dir, model="mlx-community/Remembered-4bit"), provider=provider
    )

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()

        assert isinstance(app.active_panel, DownloadPanel)
        assert provider.downloads == []
        assert provider.ensure_calls == 0
        assert any("not downloaded locally" in msg for msg in system_messages(app))


@pytest.mark.asyncio
async def test_tui_runner_starts_the_remembered_model_when_it_is_downloaded(temp_dir):
    provider = HubProviderFake(
        model="mlx-community/Remembered-4bit",
        hub_dir=temp_dir,
        installed=["mlx-community/Remembered-4bit"],
    )
    app = AgentApp(
        make_hub_config(temp_dir, model="mlx-community/Remembered-4bit"), provider=provider
    )

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()

        assert app.active_panel is None
        assert provider.ensure_calls == 1


@pytest.mark.asyncio
async def test_tui_runner_shows_live_download_progress_then_applies_the_model(temp_dir):
    provider = HubProviderFake(
        model="mlx-community/Remembered-4bit",
        hub_dir=temp_dir,
        installed=[],
        snapshots=[
            DownloadProgress(downloaded_bytes=100 * 1024**2, total_bytes=400 * 1024**2),
            DownloadProgress(downloaded_bytes=300 * 1024**2, total_bytes=400 * 1024**2),
        ],
    )
    app = AgentApp(
        make_hub_config(temp_dir, model="mlx-community/Remembered-4bit"), provider=provider
    )

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        panel = app.active_panel
        assert isinstance(panel, DownloadPanel)

        # Selecting the remembered entry starts the download and streams progress.
        options = panel.query_one("#download-options", OptionList)
        options.action_select()
        await pilot.pause()

        download = provider.downloads[0]
        assert download.started

        for _ in range(40):
            if download.finished:
                break
            await pilot.pause()

        assert download.finished
        assert provider.model == "mlx-community/Remembered-4bit"
        # The switch and the warm-up share one replacing row, so the header is
        # what records the applied model; the notice settles on readiness.
        assert "mlx-community/Remembered-4bit" in header_text(app)
        assert [n.text_content() for n in notices(app)] == ["model ready"]


@pytest.mark.asyncio
async def test_tui_runner_download_progress_is_visible_in_the_menu(temp_dir):
    half = DownloadProgress(downloaded_bytes=200 * 1024**2, total_bytes=400 * 1024**2)
    provider = HubProviderFake(
        model="mlx-community/Remembered-4bit",
        hub_dir=temp_dir,
        installed=[],
        snapshots=[half] * 20,
    )
    app = AgentApp(
        make_hub_config(temp_dir, model="mlx-community/Remembered-4bit"), provider=provider
    )

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        panel = app.active_panel
        assert isinstance(panel, DownloadPanel)

        panel.query_one("#download-options", OptionList).action_select()

        progress = panel.query_one("#download-progress", Static)
        bar = panel.query_one("#download-bar", ProgressBar)
        for _ in range(40):
            await asyncio.sleep(0.02)
            await pilot.pause()
            if "200M" in render_text(progress):
                break

        assert "200M" in render_text(progress)
        assert bar.progress == 200 * 1024**2
        assert provider.downloads[0].started


@pytest.mark.asyncio
async def test_tui_runner_reports_a_failed_download_and_stays_open(temp_dir):
    provider = HubProviderFake(
        model="mlx-community/Remembered-4bit",
        hub_dir=temp_dir,
        installed=[],
        download_state="failed",
        download_error="gated repo",
    )
    app = AgentApp(
        make_hub_config(temp_dir, model="mlx-community/Remembered-4bit"), provider=provider
    )

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        panel = app.active_panel
        assert isinstance(panel, DownloadPanel)

        panel.query_one("#download-options", OptionList).action_select()

        for _ in range(40):
            progress = panel.query_one("#download-progress", Static)
            if "failed" in render_text(progress).lower():
                break
            await pilot.pause()

        assert "gated repo" in render_text(panel.query_one("#download-progress", Static))
        assert isinstance(app.active_panel, DownloadPanel)


@pytest.mark.asyncio
async def test_tui_runner_cancelling_the_download_leaves_the_model_unset(temp_dir):
    provider = HubProviderFake(
        model="mlx-community/Remembered-4bit",
        hub_dir=temp_dir,
        installed=[],
        snapshots=[DownloadProgress(downloaded_bytes=10, total_bytes=100)],
    )
    app = AgentApp(
        make_hub_config(temp_dir, model="mlx-community/Remembered-4bit"), provider=provider
    )

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        panel = app.active_panel
        assert isinstance(panel, DownloadPanel)

        panel.query_one("#download-options", OptionList).action_select()
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()

        assert provider.downloads[0].cancelled
        assert provider.model == "mlx-community/Remembered-4bit"  # unchanged
        assert not any("switched to" in m for m in system_messages(app))
        assert app.active_panel is None


@pytest.mark.asyncio
async def test_tui_runner_switching_to_a_missing_model_opens_the_download_menu(temp_dir):
    provider = HubProviderFake(
        model="mlx-community/Present-4bit",
        hub_dir=temp_dir,
        installed=["mlx-community/Present-4bit"],
    )
    app = AgentApp(make_hub_config(temp_dir, model="mlx-community/Present-4bit"), provider=provider)

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()

        await submit(app, pilot, "/model mlx-community/Absent-4bit")
        await pilot.pause()
        await pilot.pause()

        panel = app.active_panel
        assert isinstance(panel, DownloadPanel)
        assert provider.downloads == []
        # The missing model is offered as the preselected entry.
        options = panel.query_one("#download-options", OptionList)
        option_ids = [options.get_option_at_index(i).id for i in range(options.option_count)]
        assert "mlx-community/Absent-4bit" in option_ids


@pytest.mark.asyncio
async def test_tui_runner_offers_the_model_picker_when_models_are_already_downloaded(temp_dir):
    provider = HubProviderFake(
        model="",
        hub_dir=temp_dir,
        installed=["mlx-community/Present-4bit", "mlx-community/Other-4bit"],
    )
    app = AgentApp(make_hub_config(temp_dir, model=""), provider=provider)

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()

        panel = app.active_panel
        assert isinstance(panel, ModelPanel)


@pytest.mark.asyncio
async def test_tui_runner_shift_enter_inserts_newline_and_enter_submits(temp_dir):
    """shift+enter grows the prompt upwards; plain enter submits the prompt."""
    config = Config(provider="openai", model="gpt-4o", api_key="test", session_dir=temp_dir)
    app = AgentApp(config, provider=LLMProviderFake([]))

    async with app.run_test() as pilot:
        await pilot.pause()

        input_widget = prompt_input(app)
        await pilot.press("l", "i", "n", "e", "1")
        await pilot.press("shift+enter")
        await pilot.pause()

        assert input_widget.text == "line1\n"
        assert input_widget.document.line_count == 2

        await pilot.press("l", "i", "n", "e", "2")
        await pilot.press("enter")
        await pilot.pause()

        chat = app.query_one("#chat-view")
        user_messages = [w for w in chat.query(MessageWidget) if w.role == "user"]
        assert any(w.text_content() == "line1\nline2" for w in user_messages)
        assert input_widget.text == ""


@pytest.mark.asyncio
async def test_tui_runner_copy_last_reply_notifies_and_copies(temp_dir, monkeypatch):
    """ctrl+o copies the most recent assistant reply."""
    copied: list[str] = []
    monkeypatch.setattr("textual.app.App.copy_to_clipboard", lambda self, text: copied.append(text))

    config = Config(provider="openai", model="gpt-4o", api_key="test", session_dir=temp_dir)
    provider = LLMProviderFake([make_text_events("the reply")], name="openai", model="gpt-4o")
    app = AgentApp(config, provider=provider)

    async with app.run_test(notifications=True) as pilot:
        await pilot.pause()

        await submit(app, pilot, "hello")
        await wait_for_idle(app, pilot)

        await pilot.press("ctrl+o")
        await pilot.pause()
        await pilot.pause()

        assert copied == ["the reply"]
        assert any("Copied last reply" in str(t.render()) for t in app.query(Toast))

        # With nothing to copy, the user is told instead.
        await submit(app, pilot, "/clear ")
        await pilot.press("ctrl+o")
        await pilot.pause()
        await pilot.pause()
        assert any("No reply to copy" in str(t.render()) for t in app.query(Toast))


@pytest.mark.asyncio
async def test_tui_runner_enter_runs_highlighted_suggestion_in_one_press(temp_dir):
    """A single Enter on a suggestion both applies and runs it."""
    config = Config(provider="openai", model="gpt-4o", api_key="test", session_dir=temp_dir)
    app = AgentApp(config, provider=LLMProviderFake([]))

    async with app.run_test() as pilot:
        await pilot.pause()

        prompt_input(app)
        await pilot.press("/", "h", "e")
        await pilot.pause()

        prompt = app.query_one("#prompt-input", PromptInput)
        option_list = prompt.query_one("#suggestions", OptionList)
        assert option_list.display

        await pilot.press("enter")
        await pilot.pause()

        assert any("ctrl+c quit" in msg for msg in system_messages(app))
        assert not option_list.display


@pytest.mark.asyncio
async def test_tui_runner_clicking_a_suggestion_runs_it(temp_dir):
    """Clicking a suggestion applies and runs it — no second Enter needed."""
    config = Config(provider="openai", model="gpt-4o", api_key="test", session_dir=temp_dir)
    app = AgentApp(config, provider=LLMProviderFake([]))

    async with app.run_test() as pilot:
        await pilot.pause()

        prompt_input(app)
        await pilot.press("/", "h", "e")
        await pilot.pause()

        prompt = app.query_one("#prompt-input", PromptInput)
        option_list = prompt.query_one("#suggestions", OptionList)
        option_list.action_select()
        await pilot.pause()

        assert any("ctrl+c quit" in msg for msg in system_messages(app))
        assert not option_list.display


@pytest.mark.asyncio
async def test_tui_runner_model_choices_offer_inline_dropdown(temp_dir):
    """/model shows models above the input; picking one switches directly."""
    config = Config(provider="openai", model="gpt-4o", api_key="test", session_dir=temp_dir)
    provider = LLMProviderFake(
        [],
        name="openai",
        model="gpt-4o",
        available_models=["gpt-4o", "gpt-5"],
    )
    app = AgentApp(config, provider=provider)

    async with app.run_test() as pilot:
        await pilot.pause()

        prompt = app.query_one("#prompt-input", PromptInput)
        prompt.set_models(["gpt-4o", "gpt-5"])
        option_list = prompt.query_one("#suggestions", OptionList)

        prompt_input(app)
        await pilot.press("/", "m", "o", "d", "e", "l", " ", "g", "p", "t", "-", "5")
        await pilot.pause()

        assert option_list.display
        options = [
            option_list.get_option_at_index(i).prompt for i in range(option_list.option_count)
        ]
        assert "gpt-5" in options

        # One Enter applies the highlighted model and switches to it.
        await pilot.press("enter")
        await pilot.pause()

        assert app.agent.provider.model == "gpt-5"
        assert any("switched to gpt-5" in msg for msg in system_messages(app))
        assert not option_list.display


@pytest.mark.asyncio
async def test_tui_runner_clicking_anywhere_focuses_the_prompt(temp_dir):
    """A click in the chat area moves the caret into the prompt input."""
    config = Config(provider="openai", model="gpt-4o", api_key="test", session_dir=temp_dir)
    app = AgentApp(config, provider=LLMProviderFake([]))

    async with app.run_test() as pilot:
        await pilot.pause()

        chat = app.query_one("#chat-view", ChatView)
        chat.add_system_message("clickable area")
        await pilot.pause()
        # A modal may have taken focus during startup; give it back.
        app.set_focus(None)
        await pilot.pause()

        await pilot.click("#chat-view")
        await pilot.pause()

        focused = app.focused
        assert isinstance(focused, TextArea)


@pytest.mark.asyncio
async def test_tui_runner_menu_replaces_the_input_line_instead_of_a_window(temp_dir):
    """Menus render between the input rules — no floating window over the chat."""
    config = Config(provider="openai", model="gpt-4o", api_key="test", session_dir=temp_dir)
    app = AgentApp(config, provider=LLMProviderFake([]))

    async with app.run_test() as pilot:
        await pilot.pause()

        chat = app.query_one("#chat-view", ChatView)
        chat.add_system_message("still visible")
        await submit(app, pilot, "/model ")
        await pilot.pause()
        panel = app.active_panel
        assert isinstance(panel, ModelPanel)

        # No modal screen: the menu is a widget in the input dock, and the
        # prompt line is swapped out while it is up.
        assert len(app.screen_stack) == 1
        assert panel.parent is not None
        assert panel.parent.id == "input-container"
        assert not app.query_one("#prompt-input", PromptInput).display

        # The chat keeps rendering above the menu.
        assert any("still visible" in msg for msg in system_messages(app))

        # Escape returns to input mode.
        await pilot.press("escape")
        await pilot.pause()
        assert app.active_panel is None
        assert app.query_one("#prompt-input", PromptInput).display
        assert isinstance(app.focused, TextArea)


@pytest.mark.asyncio
async def test_tui_status_bar_shows_ttft_after_a_turn(temp_dir):
    """TTFT must be visible in the TUI, not just measurable in the runtime."""

    config = Config(
        provider="openai",
        model="gpt-4o",
        api_key="test",
        session_dir=temp_dir,
    )
    app = AgentApp(config, provider=LLMProviderFake([make_text_events("Hello there")]))

    async with app.run_test() as pilot:
        await pilot.pause()
        await submit(app, pilot, "hi")
        await wait_for_idle(app, pilot)

        text = status_left_text(app)
        assert "ttft" in text
        # The prompt cost behind that TTFT is shown too, so a regression is
        # attributable without opening a debugger.
        assert "t" in text
