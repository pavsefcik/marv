# Delivery: CLI, headless, and the TUI boundary

Delivery is the outermost layer of the system. The CLI lives in
`src/marv/cli/` and is the lighter delivery surface alongside the Textual TUI.

The important architectural point:

- `marv.runtime` owns the agent loop and session/model/tool behavior
- `marv.extensions` is an optional hook host on top of the runtime
- delivery layers decide how users interact with that runtime

So the same agent can be delivered through multiple shells without changing the
runtime itself.

## Structure

The CLI is split by role rather than by framework detail:

- command surface and dispatch
- headless stdout delivery
- session utility commands

## Delivery modes

### Interactive handoff / TUI

- Implementation: `src/marv/tui/`
- Entry from the CLI: `marv run` with no prompt/headless flag

`marv run` with no prompt/headless flag creates config + provider + session
state, then hands off to the Textual TUI.

The TUI is the richest delivery surface because it supports:

- interactive chat
- model/session controls
- extension-owned UI prompts and widgets
- incremental rendering of thinking, text, and tool activity

A prompt starting with `!` runs the rest as a shell command through the agent's
`bash` tool and prints the output in the chat instead of sending it to the
model. It is an explicit user action, so it does not consult the approval
policy; output is local chat history, never a turn.

### Headless mode

- Implementation: `src/marv/cli/headless.py`
- Entry: `marv run --headless "..."` or `make run-headless`

It builds the same runtime stack, optionally attaches the extension host, and
renders runtime chunks directly to stdout:

- thinking markers
- streamed text
- tool activity
- system messages

Images can be sent to a vision-capable model with a repeatable `--attach PATH`
flag and/or `@path` tokens in the prompt (images, or PDF pages; the same
`@path` syntax the TUI uses). A PDF attaches page 1 by default; `report.pdf:3`
selects one page and `report.pdf:2-4` a range, in either the flag or an `@path`
token. The `@path` token is stripped from the prompt and anything that is not an
existing image or PDF is left as text. Attachments fail with a clear error —
naming the install command for a PDF, and refusing pages beyond 1 without
`pdftoppm` — when a file is missing, too large, or no renderer is present (see
[`tui.md`](tui.md#attaching-files)).

In the interactive TUI there is no prompt to pair the flags with, so `--attach`
images ride the first submitted message and are cleared after it.

This is a thin delivery shell over the same runtime. It is useful for one-shot
prompts, scripting, CI or automation flows, and low-ceremony debugging.

## Delivery contract

A delivery layer is responsible for:

- loading config
- creating the provider
- loading or selecting the session
- creating the `Agent`
- optionally attaching an `ExtensionHost`
- rendering runtime output
- mapping delivery-specific controls back into runtime actions

A delivery layer should **not** own:

- the agent loop
- session semantics
- tool execution semantics
- provider behavior
- extension dispatch semantics

Those belong below delivery.

The boundary between the two shells is:

- CLI owns command parsing, headless stdout delivery, and session utility commands
- TUI owns interactive chat delivery, model/session controls, and extension-hosted UI

## Help and version

`marv help` prints a curated, marv-mlx-style overview: the tagline, one usage
line per command, and the pointer to the full option lists. `marv -h` and
`marv --help` print exactly the same text — the root group's built-in help
option is replaced by an eager flag that prints the curated text — so there is
one help surface to keep accurate.

`marv version` prints `marv <version>`, the same line the eager `marv --version`
flag prints.

The curated list is the index, not the manual: `marv <command> --help` still
renders that command's full Typer option help (for example `marv run --help`).

## Session commands

The CLI also exposes delivery-level session utilities:

- `marv fork`
- `marv tree`
- `marv sessions`
- `marv config-show`

These are delivery commands over the same JSONL session model used by the runtime and TUI.

## Lifecycle commands

Two commands manage the install itself rather than a session:

- `marv update` compares the running version with the latest GitHub release and
  reinstalls the release wheel through `uv tool install --force`. `--check`
  only reports, `--force` reinstalls even when up to date, and `-y` skips the
  prompt. The TUI offers the same update at startup (see
  [`configuration.md`](configuration.md#update-check)).
- `marv uninstall` picks the removable pieces — marv and its cache/config; the
  marv-mlx runtime, its cache, and the launcher/`~/.zshrc` line it wired up;
  the `mlx-vlm` uv tool; and models in the shared Hugging Face hub — with sizes
  shown before anything is deleted. It discovers marv-mlx wherever it lives
  (`~/.marv-mlx`, `~/.local/share/marv-mlx`, the pi launcher/extension, or a
  Homebrew install). With `gum` on PATH (installed alongside the marv-mlx
  runtime) the picker and the confirmation use it in the marv-mlx style;
  without it, a plain numbered prompt is printed instead. Nothing is selected by
  default and the hub is never touched unless explicitly chosen. Flags
  (`--marv`, `--marv-mlx`, `--mlx-vlm`, `--models`, `--all`), `--dry-run`, and
  `-y` make it scriptable without the picker.

marv also records the running process in `~/.cache/marv/instance.json`. When a
second TUI picks a model different from a live instance (or from the model
already being served), it warns that starting it will restart the shared model
server instead of doing so silently.

## Latency benchmark

`marv bench` measures time-to-first-token for the active model, because for a
local-LLM harness TTFT is the difference between an assistant and an
interruption. It runs a scripted multi-turn conversation through the real agent
loop (prompt assembly, tool schemas, transport included) and reports:

- TTFT p50 / p90
- median prefill cost (prompt + tool-schema tokens)
- **TTFT growth per turn** — a flat number means the prompt is not being
  re-prefilled in full each turn; a climbing one means it is, and that is the
  latency bug to fix first
- **prefill rate** — the learned `tokens/s` this machine prefills at, plus a
  `cold start` constant when cold turns were measured

Options: `--prompt` (repeatable, overrides the default prompts), `-n`/`--turns N`,
`--no-warmup` (include cold start), `--json`, and the usual `-m`/`-p` overrides.

### Seeding the calibration

Beyond reporting, `marv bench` **writes the calibration to `state.toml`**
(`remember_latency_fit`) for the active model. This is how a fresh machine gets
its prefill constants: the first benchmark seeds the fit that the wait predictor
and the model menu's latency tier then use. An ordinary session also refreshes
it from its own measured turns, so running `bench` is a way to seed it early
rather than a required step. A `--no-warmup` run measures the cold-start
constant too.

See [`llm.md`](llm.md#prefilldecode-calibration) for the prediction model and
the "Predictable waits" entry in [`../ROADMAP.md`](../ROADMAP.md) for where this
feeds the latency work.

## Design role

The CLI owns:

- command parsing
- config override/application
- provider creation
- session selection
- delivery dispatch

It should not own:

- the runtime agent loop
- tool execution semantics
- session semantics themselves
- extension dispatch behavior

Those stay below the delivery boundary.

When adding a new delivery mode, prefer reusing `Agent` and `ExtensionHost` and
keeping delivery-specific rendering and input handling at the edge. Avoid moving
runtime concerns upward into delivery just because one shell needs them.
