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
- `pdf` — PDF info, text, render, compress, merge, split, rotate (poppler, Ghostscript, qpdf)

## The `pdf` tool

`pdf` does real PDF work by driving three external engines instead of adding a
Python dependency: poppler (`pdfinfo`, `pdftotext`, `pdftoppm`), Ghostscript
(`gs`), and qpdf (`qpdf`). Each engine is located with `shutil.which`;
`marv doctor` reports which of them are installed, and a missing one fails the
call with the binary and the command that installs it
(`` `pdftoppm` not found. Install poppler: brew install poppler ``).

Parameters:

- `action` — `info`, `text`, `render`, `compress`, `merge`, `split`, `rotate`
- `input` — input PDF path; for `merge`, comma- or newline-separated paths
- `output` — output path. Required for `merge`; otherwise a sibling default
  (`report-compressed.pdf`, `report-rotated.pdf`, `report-split.pdf`, or
  `report-%d.pdf` for a page-by-page split). For `render` it is a file name
  *prefix*: pdftoppm appends `-1.png`
- `pages` — a range such as `1-3` or `2`, used by `render` and `split`
- `dpi` — render resolution (default 150)
- `image_format` — `png` (default) or `jpeg`
- `quality` — Ghostscript preset: `screen`, `ebook` (default), `printer`, `prepress`
- `rotate` — degrees for `rotate`: 90 (default), 180, or 270

What each action runs:

| action | command |
| --- | --- |
| `info` | `pdfinfo <input>` (metadata on stdout) |
| `text` | `pdftotext -layout <input> -` (stdout, or `output`) |
| `render` | `pdftoppm -r <dpi> -png\|-jpeg [-f first -l last] <input> <prefix>` |
| `compress` | `gs -sDEVICE=pdfwrite -dPDFSETTINGS=/<quality> -o <out> <input>` |
| `merge` | `qpdf --empty --pages <a> <b> ... -- <out>` |
| `split` | `qpdf <input> --pages <input> <pages> -- <out>`, else `qpdf <input> --split-pages <out>` |
| `rotate` | `qpdf <input> --rotate=+<angle>:1-z -- <out>` |

Rules the tool enforces:

- argv is a list run through `asyncio.create_subprocess_exec` — never a shell.
- one invocation may run 120 seconds; a timeout kills the child and says so.
- a non-zero exit raises `ToolError` carrying the engine's stderr.
- the input is never overwritten, the output's parent directory is created, and
  the produced files are reported as path + size.
- `info` and `text` without `output` answer on stdout.

`pdf` is not a read-only tool: `--read-only` narrows the active set to
`read`, `grep`, `find`, `ls`, so it is not exposed there. Under
`approval_mode=destructive`, every writing action (`render`, `compress`,
`merge`, `split`, `rotate`, and `text` with an `output`) needs approval;
`info` never does.

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
