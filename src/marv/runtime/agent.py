"""Main Agent class - orchestrates LLM, tools, and session."""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from marv.llm.events import StreamEvent, StreamOptions, ToolCallBlock
from marv.llm.events import ThinkingLevel as StreamThinkingLevel
from marv.llm.latency import LatencyFit, LatencyTracker, WaitEstimate, estimate_tokens_from_json
from marv.prompts.loader import PromptTemplateLoader
from marv.prompts.parser import ParsedCommand, expand_template, parse_command
from marv.runtime.approval import requires_approval
from marv.runtime.chunk import (
    AgentChunk,
    MessageChunk,
    TextDeltaChunk,
    ThinkingDeltaChunk,
    ToolCallChunk,
    ToolCallStartChunk,
    ToolResultChunk,
    WaitEstimateChunk,
)
from marv.runtime.context import ContextManager
from marv.runtime.context_loader import load_all_context
from marv.runtime.events import (
    AgentEndEvent,
    AgentEvent,
    AgentStartEvent,
    ContextCompactionEvent,
    MessageEndEvent,
    MessageStartEvent,
    MessageUpdateEvent,
    ModelSelectEvent,
    SessionEndEvent,
    SessionStartEvent,
    ThinkingDeltaEvent,
    ThinkingEndEvent,
    ThinkingStartEvent,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    ToolExecutionUpdateEvent,
    TurnEndEvent,
    TurnStartEvent,
)
from marv.runtime.hooks import (
    AgentHooks,
    NullHooks,
    RunControl,
    ToolCallRequest,
    ToolResultData,
)
from marv.runtime.message import (
    Message,
    Role,
    ThinkingContent,
    ToolCall,
    ToolCallStart,
    ToolResult,
)
from marv.runtime.prompt_builder import ContextFile, SystemPromptOptions, build_system_prompt
from marv.runtime.session import Session
from marv.runtime.settings import (
    AgentSettings,
    InteractionMode,
    ThinkingLevel,
    clamp_thinking_level,
    get_available_thinking_levels,
)
from marv.skills.loader import SkillLoader
from marv.tools.bash import BashTool
from marv.tools.edit import EditTool
from marv.tools.find import FindTool
from marv.tools.grep import GrepTool
from marv.tools.ls import LsTool
from marv.tools.read import ReadTool
from marv.tools.registry import ToolExecutionResult, ToolRegistry
from marv.tools.write import WriteTool

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from marv.llm.provider import LLMProvider
    from marv.runtime.approval import ToolApprover
    from marv.tools.base import BaseTool

#: Tools that only inspect the workspace. ``--read-only`` narrows the active
#: tool set to exactly these, so no mutation is reachable by the model.
READ_ONLY_TOOLS = ("read", "grep", "find", "ls")


@dataclass(slots=True)
class _StreamConsumptionState:
    """Accumulated state while consuming one provider stream turn."""

    response_content: str = ""
    thinking_content: str = ""
    provider_metadata: dict[str, Any] = field(default_factory=dict)
    tool_calls: list[ToolCall] = field(default_factory=list)
    thinking_started: bool = False
    message_started: bool = False
    first_token_seen: bool = False
    stream_error: str | None = None
    stream_aborted: bool = False


@dataclass(slots=True)
class _ToolExecutionState:
    """Accumulated state while executing streamed tool calls."""

    tool_results: list[ToolResult] = field(default_factory=list)
    cancelled: bool = False


class Agent:
    """Main agent orchestrating LLM, tools, and session."""

    __slots__ = (
        "config",
        "provider",
        "tools",
        "session",
        "_context",
        "_skill_loader",
        "_template_loader",
        "_total_tokens",
        "_hooks",
        "_in_loop",
        "_cwd",
        "_context_files",
        "_approver",
        "_latency",
        "_latency_fit",
        "_warm_models",
        "_interaction_mode",
        "_active_tools_before_chat",
    )

    def __init__(
        self,
        config: AgentSettings,
        provider: LLMProvider,
        session: Session | None = None,
        cwd: Path | None = None,
        skill_loader: SkillLoader | None = None,
        template_loader: PromptTemplateLoader | None = None,
        summarization_provider: LLMProvider | None = None,
        hooks: AgentHooks | None = None,
        approver: ToolApprover | None = None,
    ) -> None:
        """Initialize the agent.

        Args:
            config: Agent runtime settings
            provider: Pre-constructed LLM provider (delivery layer concern)
            session: Optional existing session to resume
            cwd: Working directory (defaults to Path.cwd())
            skill_loader: Optional skill loader (created if not provided)
            template_loader: Optional template loader (created if not provided)
            summarization_provider: Optional provider used for context compaction summaries
        """
        self.config = config
        self._cwd = cwd or Path.cwd()
        self.provider = provider
        self.tools = ToolRegistry()
        self.session = (
            session
            if session is not None
            else Session.new(config.session_dir, provider=provider.name, model=provider.model)
        )
        self._context = ContextManager(
            self.provider,
            config.context_max_tokens,
            summarization_provider=summarization_provider or self.provider,
        )
        self._total_tokens = 0
        self._hooks: AgentHooks = hooks or NullHooks()
        self._approver = approver
        self._in_loop = False
        #: Tool-calling by default; chat mode deactivates every tool so the
        #: model is never offered one. Agent state, not session state, so it
        #: survives ``/new``. Deliberately not persisted: every launch starts
        #: back in tools mode (AGENTS.md / user expectation).
        self._interaction_mode = InteractionMode.TOOLS
        self._active_tools_before_chat: list[str] | None = None
        self._context_files: list[ContextFile] = []
        self._latency = LatencyTracker()
        #: Per-machine calibration injected by the delivery layer (which owns
        #: ``state.toml``). Empty until one is set, so a first-ever run reports
        #: no ETA rather than a hardcoded one.
        self._latency_fit = LatencyFit()
        #: (provider, model) pairs already observed to stream in this run; the
        #: first turn for a pair may include cold weight loading.
        self._warm_models: set[str] = set()

        # Use provided loaders or create with default directories + config paths
        self._skill_loader = skill_loader or SkillLoader.with_defaults(
            extra_dirs=config.skills_dirs,
            cwd=self._cwd,
        )

        # Use provided loader or create with default directories + config paths
        self._template_loader = template_loader or PromptTemplateLoader.with_defaults(
            extra_dirs=config.prompt_template_dirs,
            cwd=self._cwd,
        )

        # Register built-in tools
        tools: list[BaseTool[Any]] = [
            ReadTool(),
            WriteTool(),
            EditTool(),
            BashTool(),
            GrepTool(),
            FindTool(),
            LsTool(),
        ]
        for tool in tools:
            self.tools.register(tool)

        # Read-only mode narrows the exposed (and executable) tool set to the
        # inspection tools, so an "explain this repo" run cannot write, edit or
        # run shell commands even if the model asks for them. This is a real
        # narrowing of the active set, not an approval prompt.
        if config.read_only:
            self.tools.set_active_tools(list(READ_ONLY_TOOLS))

        # Restore model selection from session (if any)
        self._restore_model_from_session()

        # Add system prompt if this is a new session
        if not self.session.messages:
            self._init_system_prompt()

    @property
    def _supports_tools(self) -> bool:
        """Whether the active provider can call tools (some local models cannot)."""
        return bool(getattr(self.provider, "supports_tools", True))

    @property
    def _tools_available(self) -> bool:
        """Whether tools are both provider-supported and enabled by the mode."""
        return self._supports_tools and self._interaction_mode is InteractionMode.TOOLS

    @property
    def interaction_mode(self) -> InteractionMode:
        """Current mode: tool-calling or plain chat."""
        return self._interaction_mode

    def set_interaction_mode(self, mode: InteractionMode) -> None:
        """Switch between tool-calling and plain chat.

        Chat mode deactivates every tool, so no schema is offered to the model
        and any tool call it emits anyway is refused by the registry. The
        pre-chat active set is restored on returning to tools mode, so a
        read-only narrowing survives the round trip.
        """
        if mode is self._interaction_mode:
            return
        if mode is InteractionMode.CHAT:
            self._active_tools_before_chat = self.tools.list_active_tools()
            self.tools.set_active_tools([])
        elif self._active_tools_before_chat is not None:
            self.tools.set_active_tools(self._active_tools_before_chat)
            self._active_tools_before_chat = None
        self._interaction_mode = mode
        self.refresh_system_prompt()

    @property
    def cwd(self) -> Path:
        """Active working directory for the runtime."""
        return self._cwd

    @property
    def provider_name(self) -> str:
        """Active provider name."""
        return self.provider.name

    @property
    def model_name(self) -> str:
        """Active model name."""
        return self.provider.model

    @property
    def thinking_level(self) -> ThinkingLevel:
        """Current thinking level."""
        return self.config.thinking_level

    @property
    def context_max_tokens(self) -> int:
        """Configured context token budget."""
        return self.config.context_max_tokens

    @property
    def max_output_tokens(self) -> int:
        """Configured max output tokens."""
        return self.config.max_output_tokens

    @property
    def temperature(self) -> float:
        """Configured temperature."""
        return self.config.temperature

    @property
    def session_dir(self) -> Path:
        """Directory used for persisted sessions."""
        return self.config.session_dir

    @property
    def session_id(self) -> str:
        """Current session identifier."""
        return self.session.metadata.id

    @property
    def session_parent_id(self) -> str | None:
        """Current session parent identifier, if any."""
        return self.session.metadata.parent_session_id

    def supports_thinking(self) -> bool:
        """Whether the active model/provider supports thinking output."""
        return self.provider.supports_thinking()

    async def list_models(self) -> list[str]:
        """Return models available from the current provider."""
        return await self.provider.list_models()

    def set_thinking_level(self, level: ThinkingLevel) -> None:
        """Update the configured thinking level without changing model."""
        self.config.thinking_level = level

    def list_tools(self) -> list[str]:
        """Return registered tool names."""
        return self.tools.list_tools()

    def list_active_tools(self) -> list[str]:
        """Return active tool names."""
        return self.tools.list_active_tools()

    def set_active_tools(self, names: list[str]) -> None:
        """Set active tool names and refresh prompt context."""
        self.tools.set_active_tools(names)
        self.refresh_system_prompt()

    def register_tool(self, tool: BaseTool[Any]) -> None:
        """Register a tool into the runtime and refresh prompt context."""
        self.tools.register(tool)
        self.refresh_system_prompt()

    def fork_session(self, from_message_id: str, session: Session | None = None) -> Session:
        """Fork a session from a given message and return the new session."""
        source = session or self.session
        return source.fork(from_message_id, self.session_dir)

    def _init_system_prompt(self) -> None:
        """Initialize system prompt with context using the prompt builder."""
        # Load context files (AGENTS.md from project and ancestors)
        context_files = load_all_context(
            cwd=self._cwd,
            explicit_paths=self.config.context_file_paths,
            include_ancestors=True,
        )
        # Store for later access via /context command
        self._context_files = context_files

        # Get invocable skills (those not marked as disable_model_invocation)
        skills = self._skill_loader.get_invocable_skills()

        # Build system prompt options
        options = SystemPromptOptions(
            custom_prompt=self.config.custom_system_prompt,
            selected_tools=self.tools.list_active_tools(),
            append_system_prompt=self.config.append_system_prompt,
            cwd=self._cwd,
            context_files=context_files,
            skills=skills,
            tools_available=self._tools_available,
            plain_chat=self._interaction_mode is InteractionMode.CHAT,
        )

        # Build the system prompt
        system_content = build_system_prompt(options)

        self.session.append(Message(role=Role.SYSTEM, content=system_content))

    def refresh_system_prompt(self) -> None:
        """Refresh the initial system prompt to reflect current tools/context."""
        if not self.session.messages:
            self._init_system_prompt()
            return

        first_message = self.session.messages[0]
        if first_message.role != Role.SYSTEM:
            return

        context_files = load_all_context(
            cwd=self._cwd,
            explicit_paths=self.config.context_file_paths,
            include_ancestors=True,
        )
        self._context_files = context_files
        options = SystemPromptOptions(
            custom_prompt=self.config.custom_system_prompt,
            selected_tools=self.tools.list_active_tools(),
            append_system_prompt=self.config.append_system_prompt,
            cwd=self._cwd,
            context_files=context_files,
            skills=self._skill_loader.get_invocable_skills(),
            tools_available=self._tools_available,
            plain_chat=self._interaction_mode is InteractionMode.CHAT,
        )
        self.session.replace_message(
            first_message.id,
            Message.system(build_system_prompt(options)),
        )

    async def _activate_session(self, session: Session) -> None:
        """Switch the active session and serialize lifecycle hooks."""
        previous_session = self.session
        await self._emit(
            SessionEndEvent(
                session_id=previous_session.metadata.id,
                parent_session_id=previous_session.metadata.parent_session_id,
            ),
            session=previous_session,
        )
        self.session = session
        self._restore_model_from_session()
        if not self.session.messages:
            self._init_system_prompt()
        self._total_tokens = self._context.current_tokens(self.session.messages)
        await self._emit(
            SessionStartEvent(
                session_id=self.session.metadata.id,
                parent_session_id=self.session.metadata.parent_session_id,
            ),
            session=self.session,
        )

    async def new_session(self) -> None:
        """Start a fresh session and reinitialize system prompt."""
        session = Session.new(
            self.config.session_dir, provider=self.provider.name, model=self.provider.model
        )
        await self._activate_session(session)

    async def load_session(self, session: Session) -> None:
        """Switch to an existing session and restore model selection."""
        await self._activate_session(session)

    def set_leaf(self, leaf_id: str) -> None:
        """Move the active session leaf without emitting session lifecycle events."""
        self.session.set_leaf(leaf_id)
        self._restore_model_from_session()
        self._total_tokens = self._context.current_tokens(self.session.messages)

    def set_model(
        self,
        model: str,
        source: str = "set",
    ) -> None:
        """Switch to a new model and persist selection."""
        if not model or model == self.provider.model:
            return

        previous_model = self.provider.model
        self.provider.set_model(model)

        # Clamp thinking level to model capabilities
        available = get_available_thinking_levels(model, provider=self.provider.name)
        self.config.thinking_level = clamp_thinking_level(self.config.thinking_level, available)

        # Persist selection in session entries
        self.session.append_model_change(self.provider.name, model)

        # Emit model selection event
        self._emit_model_select(
            provider=self.provider.name,
            model=model,
            previous_provider=self.provider.name,
            previous_model=previous_model,
            source=source,
        )

    @property
    def total_tokens(self) -> int:
        """Get total tokens used in current context."""
        return self._total_tokens

    @property
    def is_processing(self) -> bool:
        """Whether the agent is currently inside the run/tool loop."""
        return self._in_loop

    @property
    def context_files(self) -> list[ContextFile]:
        """Get loaded context files (AGENTS.md, etc.)."""
        return self._context_files

    @property
    def latency(self) -> LatencyTracker:
        """Per-run time-to-first-token samples.

        Delivery shells read ``latency.metrics`` to show TTFT and to attribute
        a rising TTFT to prompt growth rather than to the transport.
        """
        return self._latency

    @property
    def latency_fit(self) -> LatencyFit:
        """Per-model prefill calibration used for wait predictions."""
        return self._latency_fit

    def set_latency_fit(self, fit: LatencyFit | None) -> None:
        """Install a persisted calibration for the active model.

        The delivery layer owns ``state.toml``; the runtime only consumes the
        fit, so the runtime stays free of file-format concerns.
        """
        self._latency_fit = fit or LatencyFit()

    def refine_latency_fit(self) -> LatencyFit:
        """Fold this run's measured turns into the calibration.

        Returns the updated fit so the caller can persist it. The fit is
        monotonic across the agent's lifetime so a later run can keep
        improving the estimate.
        """
        for sample in self._latency.metrics.samples:
            self._latency_fit = self._latency_fit.with_sample(sample)
        return self._latency_fit

    async def _turn_is_cold(self) -> bool:
        """Whether this turn may pay a cold model start (weights loading).

        Only asked once per (provider, model) per agent run: a provider that
        reports it is already serving is warm, and a provider with no server
        lifecycle (cloud endpoints) is never cold.
        """
        key = f"{self.provider.name}:{self.provider.model}"
        if key in self._warm_models:
            return False
        is_serving = getattr(self.provider, "is_serving", None)
        if not callable(is_serving) or not self.provider.model:
            return False
        try:
            serving = await asyncio.to_thread(is_serving)
        except Exception:  # noqa: BLE001 - a probe failure must not block a turn
            return False
        if serving:
            self._warm_models.add(key)
            return False
        return True

    def set_hooks(self, hooks: AgentHooks) -> None:
        """Replace the active runtime hook host."""
        self._hooks = hooks

    @property
    def context_reserve_tokens(self) -> int:
        """Reserved context budget held back for responses and tools."""
        return self._context.reserve_tokens

    def list_skills(self) -> list[Any]:
        """Return loaded skills."""
        return list(self._skill_loader.skills.values())

    def list_templates(self) -> list[Any]:
        """Return loaded prompt templates."""
        return list(self._template_loader.templates.values())

    def get_system_prompt(self) -> str:
        """Return the current system prompt content."""
        for message in self.session.messages:
            if message.role == Role.SYSTEM:
                return message.content
        return ""

    async def _emit(
        self,
        event: AgentEvent,
        session: Session | None = None,
    ) -> None:
        """Emit an event to the attached hook host."""
        await self._hooks.on_event(event, session=session)

    def _schedule_emit(
        self,
        event: AgentEvent,
        session: Session | None = None,
    ) -> None:
        """Schedule an agent event if an event loop is running."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self._emit(event, session=session))

    def _emit_model_select(
        self,
        provider: str,
        model: str,
        previous_provider: str | None,
        previous_model: str | None,
        source: str,
    ) -> None:
        """Emit a model_select event if possible."""
        if previous_model == model and previous_provider == provider:
            return

        event = ModelSelectEvent(
            provider=provider,
            model=model,
            previous_provider=previous_provider,
            previous_model=previous_model,
            source=source,
        )

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        loop.create_task(self._emit(event))

    def _restore_model_from_session(self) -> None:
        """Restore model selection from session entries (if available)."""
        selection = self.session.get_model_selection()
        if not selection:
            return

        provider, model = selection
        if not model:
            return
        if provider and provider != self.provider.name:
            return

        previous_model = self.provider.model
        try:
            self.provider.set_model(model)
        except ValueError:
            return

        # Clamp thinking level to model capabilities
        available = get_available_thinking_levels(model, provider=self.provider.name)
        self.config.thinking_level = clamp_thinking_level(self.config.thinking_level, available)

        # Emit model selection event (restore)
        self._emit_model_select(
            provider=provider or self.provider.name,
            model=model,
            previous_provider=self.provider.name,
            previous_model=previous_model,
            source="restore",
        )

    async def run(
        self, user_input: str, cancel_event: asyncio.Event | None = None
    ) -> AsyncIterator[AgentChunk]:
        """Process user input and yield typed output chunks.

        Args:
            user_input: The user's message
            cancel_event: Optional event to signal cancellation

        Yields:
            AgentChunk values for streamed text/thinking, tool calls/results,
            and system messages
        """
        pending_inputs: deque[str] = deque([user_input])

        # Each run reports its own latency profile.
        self._latency.reset()

        async with self._hooks.run_scope() as run_control:
            while pending_inputs:
                run_control.reset()
                current_input = pending_inputs.popleft()

                input_resolution = await self._hooks.resolve_input(current_input)
                if input_resolution and input_resolution.block:
                    pending_inputs.extend(run_control.drain_user_messages())
                    continue

                if input_resolution and input_resolution.text is not None:
                    current_input = input_resolution.text

                current_input, _command, _used_template = self._expand_template(current_input)
                self.session.append(Message(role=Role.USER, content=current_input))

                if self._context.needs_compaction(self.session.messages):
                    original_tokens = self._context.current_tokens(self.session.messages)
                    compaction = await self._context.compact(self.session.messages)
                    self.session.append_compaction(
                        compaction.summary,
                        compaction.first_kept_id,
                        tokens_before=original_tokens,
                    )
                    compacted_tokens = self._context.current_tokens(self.session.messages)
                    await self._emit(
                        ContextCompactionEvent(
                            original_tokens=original_tokens,
                            compacted_tokens=compacted_tokens,
                        )
                    )

                await self._emit(AgentStartEvent())
                if run_control.aborted:
                    await self._emit(AgentEndEvent(messages=list(self.session.messages)))
                    pending_inputs.extend(run_control.drain_user_messages())
                    continue

                if input_resolution and input_resolution.handled:
                    if input_resolution.handled_output is not None:
                        handled_msg = Message.system(input_resolution.handled_output)
                        self.session.append(handled_msg)
                        await self._emit(MessageEndEvent(message=handled_msg))
                        yield MessageChunk(payload=handled_msg)
                else:
                    async for chunk in self._agent_loop(
                        cancel_event=cancel_event,
                        run_control=run_control,
                    ):
                        yield chunk

                await self._emit(AgentEndEvent(messages=list(self.session.messages)))
                pending_inputs.extend(run_control.drain_user_messages())

    def _expand_template(self, user_input: str) -> tuple[str, ParsedCommand | None, bool]:
        """Expand prompt template if input is a slash command.

        Format: /template-name arg1 arg2 "arg with spaces" ...

        Args:
            user_input: User input text

        Returns:
            Tuple of (expanded content, parsed command or None, template_used)
        """
        # Expand skill commands: $skill-name [args]
        stripped = user_input.strip()
        if stripped.startswith("$"):
            skill_text = stripped[1:]
            if not skill_text:
                return user_input, None, False

            parts = skill_text.split(None, 1)
            skill_name = parts[0]
            raw_args = parts[1] if len(parts) > 1 else ""

            skill = self._skill_loader.get(skill_name)
            if not skill:
                return user_input, None, False

            try:
                body = skill.read_body()
            except Exception:
                return user_input, None, False

            skill_block = (
                f'<skill name="{skill.name}" location="{skill.readme_path}">\n'
                f"References are relative to {skill.base_dir}.\n\n"
                f"{body}\n"
                "</skill>"
            )
            args = raw_args.strip()
            expanded = f"{skill_block}\n\n{args}" if args else skill_block
            return expanded, None, True

        command = parse_command(user_input)
        if not command:
            return user_input, None, False

        # Look up template by name
        template = self._template_loader.get(command.template_name)
        if not template:
            return user_input, command, False

        # Expand the template with arguments
        return expand_template(template.content, command), command, True

    def _build_stream_options(self, cancel_event: asyncio.Event | None = None) -> StreamOptions:
        """Build StreamOptions from config."""
        thinking_level: StreamThinkingLevel | None = None
        if self.config.thinking_level and self.config.thinking_level != ThinkingLevel.OFF:
            thinking_level = cast("StreamThinkingLevel", self.config.thinking_level.value)

        return StreamOptions(
            temperature=self.config.temperature,
            max_tokens=self.config.max_output_tokens,
            thinking_level=thinking_level,
            cancel_event=cancel_event,
        )

    async def _consume_stream(
        self,
        stream: AsyncIterator[StreamEvent],
        state: _StreamConsumptionState,
    ) -> AsyncIterator[AgentChunk]:
        """Consume provider stream events and emit agent output chunks."""
        async for event in stream:
            match event.type:
                case "text_start":
                    if not state.message_started:
                        await self._emit(MessageStartEvent())
                        state.message_started = True

                case "text_delta":
                    if not state.message_started:
                        await self._emit(MessageStartEvent())
                        state.message_started = True
                    self._note_first_token(state)
                    state.response_content += event.delta
                    await self._emit(MessageUpdateEvent(delta=event.delta))
                    yield TextDeltaChunk(payload=event.delta)

                case "thinking_start":
                    if not state.thinking_started:
                        await self._emit(ThinkingStartEvent())
                        state.thinking_started = True

                case "thinking_delta":
                    if not state.thinking_started:
                        await self._emit(ThinkingStartEvent())
                        state.thinking_started = True
                    self._note_first_token(state)
                    state.thinking_content += event.delta
                    await self._emit(ThinkingDeltaEvent(delta=event.delta))
                    yield ThinkingDeltaChunk(payload=ThinkingContent(text=event.delta))

                case "toolcall_start":
                    yield ToolCallStartChunk(
                        payload=ToolCallStart(
                            id=event.tool_id,
                            name=event.tool_name,
                        )
                    )

                case "toolcall_end":
                    tc_block: ToolCallBlock = event.tool_call
                    tc = ToolCall(
                        id=tc_block.id,
                        name=tc_block.name,
                        arguments=tc_block.arguments,
                    )
                    state.tool_calls.append(tc)
                    yield ToolCallChunk(payload=tc)

                case "assistant_metadata":
                    if isinstance(event.metadata, dict):
                        self._merge_provider_metadata(state, event.metadata)
                        self._apply_provider_ttft(state)

                case "error":
                    state.stream_error = event.message.error_message or "LLM stream error"
                    state.stream_aborted = event.stop_reason == "aborted"
                    break
                case "done":
                    pass

    @staticmethod
    def _merge_provider_metadata(
        state: _StreamConsumptionState,
        metadata: dict[str, Any],
    ) -> None:
        """Merge an ``assistant_metadata`` payload into the turn's accumulators.

        Nested dicts are merged so a later payload can extend an earlier one
        without discarding it (providers may emit latency and provider-specific
        artifacts separately).
        """
        for key, value in metadata.items():
            if isinstance(value, dict) and isinstance(state.provider_metadata.get(key), dict):
                state.provider_metadata[key].update(value)
            else:
                state.provider_metadata[key] = value

    def _note_first_token(self, state: _StreamConsumptionState) -> None:
        """Record time-to-first-token for the current turn, once."""
        if state.first_token_seen:
            return
        state.first_token_seen = True
        self._latency.note_first_token()
        self._warm_models.add(f"{self.provider.name}:{self.provider.model}")
        self._apply_provider_ttft(state)

    def _apply_provider_ttft(self, state: _StreamConsumptionState) -> None:
        """Prefer a provider-reported TTFT and schema cost over local estimates."""
        report = state.provider_metadata.get("latency")
        if not isinstance(report, dict) or not report.get("streamed"):
            return
        ttft_ms = report.get("ttft_ms")
        if isinstance(ttft_ms, int | float):
            self._latency.refine_last_ttft(float(ttft_ms))
        schema_tokens = report.get("schema_tokens")
        if isinstance(schema_tokens, int) and not isinstance(schema_tokens, bool):
            self._latency.refine_last_schema_tokens(schema_tokens)

    async def _execute_tool_calls(
        self,
        tool_calls: list[ToolCall],
        cancel_event: asyncio.Event | None,
        state: _ToolExecutionState,
        run_control: RunControl,
    ) -> AsyncIterator[AgentChunk]:
        """Execute tool calls and emit tool result chunks."""
        for tool_call in tool_calls:
            if cancel_event and cancel_event.is_set():
                state.cancelled = True
                break

            await self._emit(
                ToolExecutionStartEvent(
                    tool_call_id=tool_call.id,
                    tool_name=tool_call.name,
                    args=tool_call.arguments,
                )
            )

            block_result = await self._hooks.authorize_tool_call(
                ToolCallRequest(
                    tool_name=tool_call.name,
                    tool_call_id=tool_call.id,
                    input=tool_call.arguments,
                )
            )

            if block_result and block_result.block:
                result = f"Tool blocked: {block_result.reason or 'blocked by policy'}"
                is_error = True
            elif requires_approval(self.config.approval_mode, tool_call.name, tool_call.arguments):
                if await self._approve_tool_call(tool_call):
                    exec_result = await self._execute_tool_with_retry(tool_call)
                    result = exec_result.content
                    is_error = exec_result.is_error
                else:
                    result = self._approval_denied_message(tool_call)
                    is_error = True
            else:
                exec_result = await self._execute_tool_with_retry(tool_call)
                result = exec_result.content
                is_error = exec_result.is_error

            mod = await self._hooks.process_tool_result(
                ToolResultData(
                    tool_name=tool_call.name,
                    tool_call_id=tool_call.id,
                    content=result,
                    is_error=is_error,
                )
            )
            if mod:
                if mod.content is not None:
                    result = mod.content
                if mod.is_error is not None:
                    is_error = mod.is_error

            await self._emit(
                ToolExecutionEndEvent(
                    tool_call_id=tool_call.id,
                    tool_name=tool_call.name,
                    result=result,
                    is_error=is_error,
                )
            )

            self.session.append(
                Message(
                    role=Role.TOOL,
                    content=result,
                    tool_call_id=tool_call.id,
                )
            )

            tr = ToolResult(
                tool_call_id=tool_call.id,
                name=tool_call.name,
                result=result,
                is_error=is_error,
            )
            state.tool_results.append(tr)
            yield ToolResultChunk(payload=tr)

    async def _approve_tool_call(self, tool_call: ToolCall) -> bool:
        """Ask the approver whether a tool call may proceed."""
        if self._approver is None:
            return False
        return await self._approver.approve(
            ToolCallRequest(
                tool_name=tool_call.name,
                tool_call_id=tool_call.id,
                input=tool_call.arguments,
            )
        )

    def _approval_denied_message(self, tool_call: ToolCall) -> str:
        """Build the tool result shown when approval is required but not granted."""
        if self._approver is None:
            return (
                f"Tool denied: '{tool_call.name}' requires approval "
                f"(approval_mode={self.config.approval_mode.value}) "
                "but no approver is available"
            )
        return f"Tool denied: '{tool_call.name}' was not approved"

    async def _execute_tool_with_retry(
        self,
        tool_call: ToolCall,
    ) -> ToolExecutionResult:
        """Execute a tool call once, with a single retry for retryable failures."""
        exec_result = await self.tools.execute(tool_call.name, tool_call.arguments)
        err = exec_result.error
        if not exec_result.is_error or err is None:
            return exec_result

        # Unknown tool/validation errors are deterministic; skip retries.
        if err.kind in ("unknown_tool", "validation") or not err.retryable:
            return exec_result

        await self._emit(
            ToolExecutionUpdateEvent(
                tool_call_id=tool_call.id,
                tool_name=tool_call.name,
                partial_result=f"Retrying tool after {err.kind} error: {err.message}",
            )
        )
        return await self.tools.execute(tool_call.name, tool_call.arguments)

    async def _agent_loop(
        self,
        cancel_event: asyncio.Event | None = None,
        run_control: RunControl | None = None,
    ) -> AsyncIterator[AgentChunk]:
        """Run the agent loop with tool execution."""
        max_iterations = 25  # Prevent infinite loops
        if run_control is None:
            async with self._hooks.run_scope() as nested_run_control:
                async for chunk in self._agent_loop(
                    cancel_event=cancel_event,
                    run_control=nested_run_control,
                ):
                    yield chunk
            return
        self._in_loop = True

        try:
            for turn in range(max_iterations):
                if run_control.aborted or (cancel_event and cancel_event.is_set()):
                    break

                await self._emit(TurnStartEvent(turn_number=turn))

                messages_for_llm = await self._hooks.prepare_context(list(self.session.messages))
                options = self._build_stream_options(cancel_event=cancel_event)
                tool_schemas = self.tools.get_schemas() if self._tools_available else None

                # Anchor the TTFT clock for this turn. The prompt estimate is the
                # same token counter compaction uses, so the number shown to the
                # user matches the number that drives context management. Tool
                # schemas are on the wire every turn and cost prefill too, so
                # they are threaded through here (not dropped) and the wait
                # prediction is emitted *before* the request so the UI can
                # narrate the prefill from its first second.
                prompt_tokens = self.provider.count_messages_tokens(messages_for_llm)
                schema_tokens = estimate_tokens_from_json(tool_schemas) if tool_schemas else 0
                cold = await self._turn_is_cold()
                self._latency.note_request(
                    prompt_tokens=prompt_tokens,
                    schema_tokens=schema_tokens,
                    cold=cold,
                )
                yield WaitEstimateChunk(
                    payload=WaitEstimate(
                        prompt_tokens=prompt_tokens,
                        schema_tokens=schema_tokens,
                        eta_ms=self._latency_fit.predict_ttft_ms(
                            prompt_tokens, schema_tokens, cold=cold
                        ),
                        cold=cold,
                    )
                )
                stream = self.provider.stream(
                    messages_for_llm,
                    tools=tool_schemas,
                    options=options,
                )

                stream_state = _StreamConsumptionState()
                async for chunk in self._consume_stream(stream, stream_state):
                    yield chunk

                if stream_state.stream_error:
                    # The turn never streamed: drop the pending sample so it is
                    # not attributed to whichever turn streams next.
                    self._latency.discard_pending()
                    if not stream_state.stream_aborted:
                        error_msg = Message.system(
                            f"[LLM stream error]\n{stream_state.stream_error}"
                        )
                        self.session.append(error_msg)
                        await self._emit(MessageEndEvent(message=error_msg))
                        yield MessageChunk(payload=error_msg)
                        await self._emit(TurnEndEvent(message=None, tool_results=[]))
                    break

                if stream_state.thinking_started:
                    await self._emit(ThinkingEndEvent(content=stream_state.thinking_content))

                thinking_obj = (
                    ThinkingContent(text=stream_state.thinking_content or "")
                    if stream_state.thinking_content
                    else None
                )
                assistant_msg = Message(
                    role=Role.ASSISTANT,
                    content=stream_state.response_content,
                    tool_calls=stream_state.tool_calls if stream_state.tool_calls else None,
                    thinking=thinking_obj,
                    provider_metadata=stream_state.provider_metadata or None,
                    provider=self.provider.name,
                    model=self.provider.model,
                )

                self.session.append(assistant_msg)
                await self._emit(MessageEndEvent(message=assistant_msg))

                if not stream_state.tool_calls:
                    await self._emit(TurnEndEvent(message=assistant_msg, tool_results=[]))
                    break

                tool_state = _ToolExecutionState()
                async for chunk in self._execute_tool_calls(
                    stream_state.tool_calls,
                    cancel_event,
                    tool_state,
                    run_control,
                ):
                    yield chunk

                await self._emit(
                    TurnEndEvent(
                        message=assistant_msg,
                        tool_results=tool_state.tool_results,
                    )
                )

                if (
                    run_control.aborted
                    or tool_state.cancelled
                    or (cancel_event and cancel_event.is_set())
                ):
                    break

            self._total_tokens = self._context.current_tokens(self.session.messages)
        finally:
            self._in_loop = False

    async def compact(self) -> None:
        """Manually trigger context compaction."""
        original_tokens = self._context.current_tokens(self.session.messages)
        compaction = await self._context.compact(self.session.messages)
        self.session.append_compaction(
            compaction.summary,
            compaction.first_kept_id,
            tokens_before=original_tokens,
        )
        self._total_tokens = self._context.current_tokens(self.session.messages)

    async def close(self) -> None:
        """Clean up resources."""
        await self._emit(
            SessionEndEvent(
                session_id=self.session.metadata.id,
                parent_session_id=self.session.metadata.parent_session_id,
            ),
            session=self.session,
        )
        await self.provider.close()
