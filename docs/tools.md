# Tools

Tools are Pydantic‑typed units of capability with OpenAI‑style JSON schemas. The registry lives in `src/marv/tools/registry.py`.

## Built‑in tools

- `read` — read a file with line numbers
- `write` — create or overwrite a file
- `edit` — exact find/replace edits
- `bash` — run shell commands
- `grep` — regex search (uses `rg` when available)
- `find` — glob search
- `ls` — list directories

## How tools are executed

1. The model returns a tool call with `name` + JSON args.
2. The registry validates args via Pydantic.
3. The tool executes and returns a string result (or raises `ToolError`).
4. The registry wraps output into `ToolExecutionResult { content, is_error }`.
5. Tool results are appended as `Role.TOOL` messages.

`ToolError` raised by a tool is caught by the registry and returned as a
`ToolExecutionResult { content, is_error=True, error }`; the string result of a
successful run comes back with `is_error=False`.

## Extending tools

Extensions can register tools in two ways:

- `ExtensionAPI.register_tool()` during extension setup
- `ctx.tools.register(tool)` at runtime

Once registered, they are treated exactly like built‑ins and appear in the system prompt tool list for future turns.

## Active tools

The registry also tracks an active tool subset.

- By default, all registered tools are active.
- `ctx.tools.set_active([...])` lets extensions narrow the tools exposed to the model.
- The system prompt is refreshed when the active tool set changes.
- Inactive tools are omitted from provider tool schemas and cannot be executed through the registry.

## Chat mode

The TUI can switch the agent into a plain chat with no tools at all:

- `/chat` deactivates every tool, so no schema is sent to the model and any
tool call it emits anyway is refused by the registry as an inactive tool. The
system prompt becomes a plain, non-coding assistant prompt: no tool list, no
guidelines, no skills, and no project context files. The date is kept; the
working directory is dropped.
- `/tools` restores the previous active set, so a `read_only` narrowing survives
the round trip.

The mode lives on the agent (`InteractionMode` in `src/marv/runtime/settings.py`),
not the session: `/new` keeps the last choice, while a fresh launch starts in
tools mode because it is not persisted.
