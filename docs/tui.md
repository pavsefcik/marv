# TUI (Textual)

The interactive terminal UI is built with Textual (`src/marv/tui/*`).

## Internal structure

The TUI is split into a few clear roles:

- shell/lifecycle
- runtime composition
- delivery command handling
- chat/session rendering
- extension UI hosting

The widget and panel files sit underneath that split as the leaf UI components.

Menus are inline: the session picker, model/thinking menu, context viewer,
download picker, and extension prompt/confirm/select/presented panels render at
the input line, between the two rules around the prompt, and grow upward as
needed. They never float over the chat. Picking an item applies it and drops
straight back into input mode; `Esc` cancels.

## Features

- Streaming assistant output + tool calls
- Skill and slash‑command autocomplete
- Status bar showing model + token usage + generation speed + thinking level + RAM usage + extension status
- Model selector, model-download picker with live progress, and context viewer
- Extension UI host for `notify`, `input`, `confirm`, `select`, `present(view)`, and persistent widgets

### Legible waits

A local model can send nothing for minutes while it prefills, so the waiting
indicator narrates the wait instead of spinning opaquely:

```
⠹ prefilling 32k tokens · ~2:00 · 1:14 elapsed (62%)
```

The `~` figure and the percentage are a *revising* estimate, recomputed on
every tick from elapsed against the prediction — not a progress bar that jumps
or freezes at 99%. When the estimate is exceeded the indicator says
`taking longer than predicted`, which is information about the machine rather
than a failed bar.

The prediction comes from this machine's own measured turns (see
[`llm.md`](llm.md#prefill-calibration)). A first-ever run has no measured
history, so the indicator degrades to elapsed-only (`prefilling 32k tokens ·
1:14 elapsed`) — no ETA and no hardcoded constant is ever shown.

When the predicted wait crosses ~20 s, the indicator also attributes it, so the
wait is a decision rather than just a fact:

- conversation-dominated → "`/compact` would summarize older turns and shorten it"
- schema-dominated → the tool schemas are named, with the schema-light option
- a cold start → said plainly, with no fix claimed (nothing to shrink)

An ordinary turn never shows this advice. While generating, the footer shows an
`elapsed` timer next to the measured `ttft`.

## Appearance

The TUI follows the terminal rather than imposing a palette. The default
`theme = "auto"` probes the terminal background (OSC 11, then the system
appearance) and applies Textual's built-in `ansi-dark` or `ansi-light` theme, so
marv matches the terminal it runs in. Any built-in Textual theme name (`nord`,
`tokyo-night`, `dracula`, …) can be selected instead, and `minimal` keeps the
old Nord-ish palette available. An unknown name falls back to `auto` with a
warning instead of failing to start.

Set the theme in config (`theme = "nord"`), with `AGENT_THEME`, or per run with
`marv run --theme <name>`.

Layout is intentionally quiet, following the conventions of modern terminal
agents:

- transparent chrome, with thin rules rather than filled panels or per-message
  borders
- tool calls render as a single `✓ Reading  src/parser.py` row (`✓` on success,
  `✕` on error, a braille spinner while running) with the output indented
  beneath it
- thinking renders as a dim italic `▾ Thought` row that can be collapsed
- the startup header is a compact two-line summary instead of an ASCII banner

Styling uses theme variables (`$primary`, `$text-muted`, …) so a theme swap
needs no CSS change. Note that Textual resolves theme variables in CSS and in
markup strings, but **not** in `rich.text.Text` styles, and that `$text-muted` /
`$text-disabled` carry alpha and therefore cannot be used as border colors (use
`$foreground-muted`).

### Snapshots

The presentation is pinned by committed text snapshots in
`tests/delivery/tui/snapshots/`. `test_snapshot_behavior.py` renders the whole
screen through Textual's compositor and compares it against the committed frame,
normalizing volatile values (version, session id, RAM). The styled snapshots also
pin each line's resolved styles, so colour and weight regressions are caught, not
just geometry.

After an intentional visual change, review the diff and regenerate:

```sh
MARV_UPDATE_SNAPSHOTS=1 make test
```

Appearance is detected once at startup, before Textual owns the terminal. It is
deliberately not re-probed mid-session: Textual's input parser has no OSC 11
handler, so an unsolicited reply would be reissued as key input.

## Built‑in commands

- `/clear` — clear chat
- `/new` — new session
- `/load` — load a session
- `/resume` — pick a session to resume
- `/fork` — fork from a message
- `/tree` — move session leaf (tree view; linear list when no branches)
- `/context` — show context files
- `/compact` — summarize older turns to shrink the prompt on demand
- `/model` — open the model/thinking menu
- `/model <name>` — switch model directly
- `/help` — quick help
- `/quit` — exit

Model switching is routed through `Agent.set_model(...)` and provider `set_model(...)`. Invalid provider/model pairs are rejected and shown as a system message.

If the requested model is not present in the local HF hub, `/model <id>` opens the
download picker instead of switching, and the switch happens once the download
finishes. On startup, a remembered model that is missing from the hub also opens
the picker (with that model preselected) rather than downloading silently.

These commands are delivery-level commands owned by the TUI controller. They are handled before input enters the runtime hook/LLM path.

## Extension UI surfaces

When a TUI `AgentApp` is active, extensions get a bound `ctx.ui` surface. That supports:

- notifications rendered into the chat stream
- status text in the status bar
- prompt / confirm / select menus at the input line
- temporary presented custom views with view-defined controls
- persistent widget slots:
  - `footer`
  - `right_panel`

Presented views are host-framed dialogs, but the view decides which controls appear inside them. The supported control kinds are `input`, `select`, and `button`.

The bundled subagent example uses `select`, `input`, and `confirm` for launching, plus a `right_panel` widget for live progress and a presented read-only result view for inspection.

## Rendering model

The TUI does not render the main conversation from generic runtime events.

Instead:

- streamed runtime chunks drive the chat/tool/thinking UI
- extension-owned UI is driven through the bound `ctx.ui` bridge

That split keeps the main conversation rendering incremental and presentation-focused, while leaving lifecycle/event reactions to the extension host.

## Design role

The TUI owns:

- interactive delivery behavior
- focus/cancel state
- model/session controls
- rendering streamed runtime chunks
- hosting extension-owned prompts, views, and widgets

It should not own:

- the runtime loop
- session semantics
- tool execution semantics
- extension dispatch itself

## Keybindings

- `Ctrl+C` — quit
- `Ctrl+L` — clear
- `Ctrl+O` — copy the most recent assistant reply to the clipboard (OSC 52)
- `Esc` — cancel or refocus input
