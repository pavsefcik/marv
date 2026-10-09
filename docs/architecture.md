# Architecture Overview

This project is intentionally modular so each piece can be read and tested in isolation.

## Module layout

```
runtime/     Agent loop, sessions, context compaction, prompts
llm/         Provider adapters (mlx-vlm + Apple FM + OpenAI-compatible) + streaming events
config/      Runtime config loading
tools/       Built-in tool registry + implementations
skills/      Skill discovery + validation
prompts/     Prompt templates + argument expansion
extensions/  Event hooks + runtime/session/model/tool/UI host
tui/         Textual UI (interactive mode)
cli/         Typer command surface + headless/session helpers
```

## Delivery layer

Delivery is the outermost layer of the system.

It owns:

- command parsing and mode selection
- user interaction surfaces
- rendering streamed runtime output
- mapping delivery controls back into runtime/session/model actions

It does not own:

- the agent loop
- session semantics
- tool execution semantics
- provider behavior
- extension dispatch semantics

The same runtime stack can be exposed through multiple delivery shells:

- Textual TUI
- headless CLI

See [`cli.md`](cli.md) for the delivery model.

## Bootstrap and composition

Bootstrap sits between delivery and runtime. There is no single `bootstrap.py`;
the concern is spread across the modules named here.

It owns:

- loading and merging config (`src/marv/config/runtime.py`)
- projecting delivery config into runtime settings (`Config.to_agent_settings()`)
- resolving and constructing the active LLM provider (`src/marv/llm/factory.py`)
- assembling the runtime stack for a delivery shell (`src/marv/tui/compose.py`,
  `src/marv/cli/headless.py`)

## Runtime

The runtime is the execution center of the system.

It owns:

- the agent loop
- session state and branching
- context loading and compaction
- system prompt construction
- model/tool execution flow
- runtime lifecycle events

It exposes a small neutral hook boundary so integrations can:

- resolve input
- prepare context
- authorize tool calls
- process tool results
- observe runtime events
- keep run-scoped control state

## Agent loop

The agent loop is implemented in `src/marv/runtime/agent.py` and is built around
streaming responses and tool execution.

### Step 1: Input intake & preprocessing

- The runtime first asks its hook host to resolve raw input via `resolve_input(...)`.
- That hook can:
  - block the input
  - replace the text
  - fully handle the input and return output without calling the LLM
- After hook-based input resolution, built-in preprocessing runs:
  - `$skill-name` expands into a `<skill>` block + optional args
  - `/template-name args` expands using prompt templates
- The extension host uses input resolution to implement extension slash commands.
- Hook hosts can queue follow-up user messages with run-scoped control, so one
  command can trigger later LLM turns.

### Step 2: Persist user message + compaction check

- The user message is appended to the JSONL session.
- The `ContextManager` checks token usage and triggers compaction when needed.

### Step 3: Build system prompt + call LLM

- System prompt is composed from:
  - base prompt or custom system prompt
  - active tool descriptions + guidelines
  - context files (`AGENTS.md`)
  - skills (XML list)
  - environment info
- The model is called via a provider-specific event stream.

### Step 4: Stream + tool execution

- Text, thinking, and toolcall events stream back.
- Tool calls are accumulated, validated, and executed via the tool registry.
- Hook hosts can block tools or modify tool results before results are appended.

### Step 5: Turn finalization

- Assistant message + tool results are appended to the session.
- Turn, agent, session, model-select, and compaction lifecycle events are emitted.
- Any queued extension follow-up user messages are drained back into the run loop.
- Token counts are updated for the status bar.

### Cancellation

- A shared `asyncio.Event` lets the UI/CLI cancel a running stream or tool loop.

## LLM and streaming

The LLM layer is a supporting subsystem under the runtime.

It owns:

- provider implementations
- streaming event contracts
- model capability and policy helpers
- provider/model validation rules

## Tooling

The tools layer owns the tool registry, built-in tools, schema validation, and active-tool scoping.

## Skills + prompts

These are built-in input/preprocessing subsystems.

They own:

- skill discovery and validation
- prompt template loading and argument expansion
- preprocessing that happens after hook-based input resolution and before the model call

## Extensions

Extensions are one implementation of the runtime hook boundary.

They own:

- extension loading
- handler registration and dispatch
- extension-facing event/result types
- extension author APIs (`ctx.session`, `ctx.model`, `ctx.tools`, `ctx.runtime`, `ctx.ui`)
- extension-owned mutable state such as run-scoped control and optional UI bindings

## Delivery shells

There are two delivery shells:

- a Textual TUI
- a Typer CLI with headless mode and session utilities

Both sit on top of the same runtime and extension host.
