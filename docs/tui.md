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

Menus are inline: the session picker, model/thinking menu, theme menu, context
viewer, download picker, and extension prompt/confirm/select/presented panels render at
the input line, between the two rules around the prompt, and grow upward as
needed. They never float over the chat. Picking an item applies it and drops
straight back into input mode; `Esc` cancels.

## Features

- Streaming assistant output + tool calls
- `@path` image/PDF attachment (see [Attaching files](#attaching-files))
- Skill, slash‑command, and `@path` autocomplete
- Status bar showing model + token usage + generation speed + thinking level + RAM usage + extension status
- Model selector, model-download picker with live progress, and context viewer
- Extension UI host for `notify`, `input`, `confirm`, `select`, `present(view)`, and persistent widgets

### Attaching files

Type `@path` (or `@"path with spaces"`) in the prompt to attach an image (`png`,
`jpg`, `gif`, `webp`, `bmp`, `heic`, `tif`, …) or a PDF to the turn. A PDF
attaches page 1 by default; append `:3` for one page or `:2-4` for a range. The
token is removed from the prompt and each attached image is streamed to a
vision-capable model as a content part; the chat shows a dim `[image: name]`
line under the prompt, and replaying a session restores it. A token that is not
an existing image or PDF is left as ordinary text. When the loaded model cannot
accept images (e.g. `apple-fm`), the turn runs text-only with a system warning.

Attachment bytes are cached content-addressed under the session's
`attachments/` directory and referenced by hash from the message, so pixels are
not copied into the session JSONL. The blob is read back when a session is
replayed; a blob that has gone missing simply drops that image from the turn.

Typing `@` opens the same dropdown as `/` and `$`, listing entries from the
working directory: directories first (a directory reads `@sub/`), then names,
and dotfiles only once the partial itself starts with a dot. `Tab` fills the
path into the prompt without running it, `Enter` or a click fills it and runs
the turn. A name with whitespace is written back in its quoted form
(`@"my shot.png"`), the form the attachment parser accepts. The list shows any
entry, not just attachable ones — only an existing image or PDF becomes an
attachment, so a completed `.md` or `.py` path stays plain prompt text.

A PDF needs a renderer: `pdftoppm` (poppler, `brew install poppler`) or macOS
`sips`. `sips` renders page 1 only, so asking for any other page without
`pdftoppm` fails with a message naming the install command rather than silently
sending page 1. At most 10 pages may be attached at once.

### Legible waits

A local model can send nothing for minutes while it prefills, so the waiting
indicator narrates the wait instead of spinning opaquely:

```
⠹ adding 16 tokens (2.712k cached) · ~0:02 · 0:01 elapsed (50%)
```

Only the tokens this turn actually prefills are named: with a cached prefix the
line says `adding`, not the whole prompt again. A first turn with nothing cached
reads `prefilling 32k tokens`.

The `~` figure and the percentage are a *revising* estimate, recomputed on
every tick from elapsed against the prediction — not a progress bar that jumps
or freezes at 99%. When the estimate is exceeded by more than ten seconds the
indicator says `taking longer than predicted`, which is information about the
machine rather than a failed bar. A few seconds over is estimate noise and is
not called out.

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

The default `theme = "marv-mlx"` applies the marv family's own palette (the
magenta wordmark, dim greys and green/yellow markers) on the same terminal-native
background as `ansi-dark`, so the window shows through instead of marv drawing a
black box inside it. Set `theme = "auto"` to follow the terminal exactly: marv
probes the terminal background (OSC 11, then the system appearance) and applies
Textual's built-in `ansi-dark` or `ansi-light` theme, so marv matches the
terminal it runs in. Any built-in
Textual theme name (`nord`, `tokyo-night`, `dracula`, …) can be selected too, and
`minimal` keeps the old Nord-ish palette available. An unknown name falls back to
`auto` with a warning instead of failing to start.

Set the theme in config (`theme = "nord"`), with `AGENT_THEME`, or per run with
`marv run --theme <name>`. To switch without restarting, use the `/theme` menu:
highlighting an entry previews it on the whole screen, Escape restores the
previous palette, and Enter remembers the choice in `state.toml` (the weakest
config layer, so config/env/CLI still win). `auto` there resolves from the system
appearance rather than a fresh OSC 11 probe, which is unsafe once Textual owns
the terminal.

Layout is intentionally quiet, following the conventions of modern terminal
agents, with the marv family's shapes borrowed from marv-mlx:

- transparent chrome, with thin rules rather than filled panels or per-message
  borders
- the marv wordmark is `▌▌ marv`, and the prompt line carries the same `▌` mark
- a user prompt carries a `$primary` stripe down its left edge, so a prompt is
  never mistaken for an answer. The stripe is a border, not a background: the
  ANSI themes' `$surface`/`$panel` are transparent, and only a colour drawn for
  the message itself can mark it there
- menus use `───` separators before their escape hatches (a custom id, back to
  top), dim indented descriptions, and a `↑/↓ nav · enter select · esc back`
  footer
- tool calls render as a single `✓ Reading  src/parser.py` row (`✓` on success,
  `✗` on failure, a braille spinner while running) with the output indented
  beneath it
- thinking renders as a dim italic `▾ Thought` row that can be collapsed
- the startup header is a compact two-line summary instead of an ASCII banner,
  and it tracks the live model and thinking selection
- transient status (model switch, server warm-up, readiness) is one *replacing*
  row rather than a stack of near-duplicate system messages; the warm-up row
  leads with a braille spinner, and a settled row leads with `✓` (the `--`
  marker is reserved for permanent, historical system messages)

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

### Signing off

Quitting prints a sign-off on a fresh screen, in the marv-mlx idiom:

```
▌▌ marv says bye!
   ✓ session saved        s-1a2b3c
   ✓ model unloaded       mlx-community/Qwen3.5-4B-MLX-4bit :11500
   ✓ memory released      5.2 GB
   all systems terminated · marv 1.120.1
```

Every row is something marv observed, not a slogan. The memory figure is read
from the model server's footprint (GPU-wired weights included) *before* it is
unloaded. If nothing was serving, the block says `no model was running` instead
of claiming a teardown; if a server is somehow still answering, it says
`model still running` (with the `marv-mlx stop` hint) and never prints
`all systems terminated`.

The sign-off only runs for an interactive terminal on a clean exit: piped output
and crashes leave the screen alone. Turn it off with `goodbye = false` in config
or `AGENT_BYE=off`.

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
- `/theme` — open the theme menu (live preview; the choice is remembered)
- `/theme <name>` — switch theme directly (`auto`, `marv-mlx`, `minimal`, or any
  built-in Textual theme)
- `/chat` — plain chat mode: every tool is off and no schema is sent
- `/tools` — back to tool-calling mode (the default)
- `/kurt` — kurt mode: chat-like for now (every tool is off, no schema sent)
- `/help` — the command list, one line per command with what it does
- `/quit` — exit

Chat mode is agent state, not session state: `/new` keeps the current choice,
while a fresh launch always starts in tools mode (nothing is persisted). The
status bar shows `[chat]` (or `[kurt]`) while tools are off. See
[tools.md](tools.md#chat-mode).

Model switching is routed through `Agent.set_model(...)` and provider `set_model(...)`. Invalid provider/model pairs are rejected and shown as a system message. A successful switch updates the startup header and updates the single transient status row (`switched to …` → `starting model …` with a spinner → `🚀 all systems ready`).

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

- `Ctrl+C` — copy the mouse selection, or quit when nothing is selected
- `Ctrl+L` — clear
- `Ctrl+O` — copy the most recent assistant reply to the clipboard (OSC 52),
  the whole-reply fallback for a reply too long to select by hand
- `Esc` — cancel or refocus input

Dragging with the mouse selects text anywhere in the chat, including part of an
answer. Answers render through Textual's own `Markdown` widget, whose blocks
carry the content offsets Textual needs to resolve the cell under the pointer;
a Rich `Markdown` renderable carries none, which made every drag over an answer
select the whole reply. A click no longer copies the whole message, because that
made copying one paragraph of a long reply impossible.
