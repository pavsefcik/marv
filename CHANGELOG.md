# Changelog

All notable changes to marv are documented here. The version in
[`VERSION`](VERSION) is the single source of truth.

## [1.120.0] - 2026-10-10

One-line install, plain-chat mode, and a slimmer tree.

### Added

- **One-line `curl` installer (`install.sh`).** Preflights the machine (Apple
  Silicon, macOS 14.0+, unified memory), installs the needed toolchain
  (Xcode CLT check, Homebrew, `uv`, Python 3.14), installs the
  [marv-mlx](https://github.com/pavsefcik/marv-mlx) runtime and `mlx-vlm`, then
  installs marv as a `uv` tool from the latest release (or the local checkout)
  and puts uv's tool bin on `PATH` in `~/.zshrc`. Idempotent and re-runnable;
  `MARV_DRY_RUN=1` prints the plan without touching the system, and
  `MARV_SKIP_MLX=1` / `MARV_SKIP_HARDWARE_CHECK=1` / `MARV_NO_ZSH=1` opt out of
  individual steps. Usage: `curl -fsSL
  https://raw.githubusercontent.com/pavsefcik/marv/main/install.sh | sh`.

- **`/chat` and `/tools` — plain chat vs tool-calling mode.** `/chat` turns
  every tool off: no schema is sent, the system prompt becomes a plain
  non-coding assistant prompt (no tool list, skills, or project context), and
  a tool call the model emits anyway is refused. `/tools` restores the previous
  active set (a `read_only` narrowing survives). The mode is agent state, so
  `/new` keeps the last choice while a fresh launch always starts in tools
  mode. The status bar shows `[chat]` while tools are off.

### Changed

- **Docs consolidated; Claude/Anthropic leftovers removed.** The historical
  `PLAN.md`, `marv-merge-plan.md`, and project handoff are gone. The coworking
  proposal is folded into `ROADMAP.md` as the local-LLM coworking direction,
  `docs/delivery.md` is merged into `docs/cli.md`, and `docs/agent-loop.md`
  into `docs/architecture.md`. The `CLAUDE.md` symlink and CLAUDE.md
  context-file loading are removed — `AGENTS.md` is the only context file —
  along with the unused Anthropic/Claude model catalog and its capability
  policy. The OpenAI catalog remains for the `openai` / `openai-codex` /
  `openai-compat` providers.

## [0.111.0] - 2026-10-09

Predictable waits and a self-diagnosing setup.

### Added

- **`marv doctor` — self-diagnosing setup.** One command checks the resolved
  config, the local Hugging Face hub, whether the selected model is present,
  whether the model server is reachable and serving what you expect, the
  download tooling, the clamped output budget, and whether configured
  extensions actually load. Every check is independent, prints a one-line
  verdict, and never raises; `--json` for machines. Setup problems explain
  themselves instead of surfacing later as a cryptic TUI error.
- **`--read-only` mode.** `marv run --read-only` (or `AGENT_READ_ONLY=true`,
  or `read_only = true` in config) narrows the active tool set to
  `read`/`grep`/`find`/`ls`. This is a real narrowing — the mutating tools are
  absent from the model schemas and refused by the registry — so an "explain
  this repo" run cannot write, edit, or run shell commands even if the model
  asks. `marv config show` reports the active tool set.
- **Per-model prefill calibration.** marv now learns the two constants the wait
  prediction needs from turns it already measures: the prefill rate
  (tokens/second) and a separate **cold-start** constant for weight loading.
  Both are fit through the origin over warm samples, and a sample far below the
  fit is treated as a server-side **cache hit** rather than as a faster prefill.
  The fit is persisted per model in `state.toml` beside the last-used selection.
- **A wait estimate before the request is sent.** The agent emits a
  `WaitEstimate` (prompt tokens, tool-schema tokens, a measured `eta_ms`) on its
  output channel *before* the provider request goes out, so delivery can
  narrate the prefill instead of showing an undifferentiated spinner. Tool
  schemas — previously dropped from the latency path — are now threaded
  through, so the estimate reflects everything the server must prefill. On a
  first-ever run the estimate is honestly `None`, not a hardcoded constant.
- **The wait is narrated, and a long one offers a way out.** The TUI waiting
  indicator shows `prefilling 32k tokens · ~2:00 · 1:14 elapsed (62%)`, a
  revising estimate recomputed each tick; an overrun says `taking longer than
  predicted` rather than sitting at 99%. When the prediction crosses ~20 s it
  attributes the cost and offers an exit: `/compact` for a
  conversation-dominated wait, the schema-light alternative for a schema-
  dominated one, and a plain "cold start, nothing to shrink" for weight
  loading. The footer also gets a decode `elapsed` timer and an `eta` reading.
  An ordinary turn shows none of this.
- **`/compact` on demand.** The automatic path only fires near the context
  limit; the same summarization is now available as a command (and as the exit
  offered from a long-predicted wait), reporting how much it shrank.

### Fixed

- **Tool-schema tokens are no longer invisible to latency figures.** `Agent`
  now threads `schema_tokens` (estimated before the request and refined from the
  transport's measured `tools` payload) into the latency tracker, so the TUI
  prefill figure and `marv bench` include the schemas that are on the wire every
  turn. The long-standing "known gap" in the figures is closed.

## [0.110.0] - 2026-10-08

Docs: audit pass against the working tree — corrected the prefill-figure claim
(prompt-only; `schema_tokens` not yet threaded), documented the `marv bench`
metrics, the modal→panel rename caveats, the marv-mlx catalog format, and the
installer-vs-pi-extension divergence; added missing CHANGELOG link refs.

### Added

- **Turn-latency measurement: TTFT is now a first-class number.** A local model
  is judged on how long until the first token, and whether that gets worse as
  the conversation grows — the signature of re-prefilling the whole history
  every turn. The OpenAI-compatible transport now times each turn from request
  to first decoded token and reports it (with the prompt and tool-schema token
  cost behind it) on the existing `assistant_metadata` channel, so no new
  stream protocol was needed. `Agent` accumulates one sample per streamed turn,
  and `marv bench` runs a scripted conversation through the real agent loop and
  reports TTFT p50/p90, median prefill cost, and the change in TTFT per turn
  (`--json` for machine consumption). A turn that errors before streaming is
  excluded rather than recorded as an infinite TTFT.- **TTFT and prefill cost in the TUI status bar.** After each turn the status
  line shows `ttft <ms>/<prefill>t` next to the existing token rate, so a
  latency regression is visible where it happens.

### Fixed

- **Large-prompt turns no longer die as `[LLM stream error]` from a prefill
  timeout.** The transport's 120 s read timeout was shorter than a silent MLX
  prefill: a ~32k-token prompt on a 9B model takes ~2 minutes before the first
  token, and `mlx_vlm` sends nothing on the stream while it works, so the client
  killed the socket mid-prefill — then retried, discarding and re-running the
  entire prefill up to three more times before failing. Local-server backends
  (`marv-mlx`, `apple-fm`) now wait indefinitely by default, and a read timeout
  that fires *before* the first token is surfaced immediately instead of being
  retried (a retry only restarts the prefill). `AGENT_LLM_READ_TIMEOUT` sets a
  finite cap for any provider (`off`/`0` disables). A timeout after tokens have
  started streaming is still retried as a normal transport failure.
- **The download picker showed a stale curated list.** It rendered a hardcoded
  table that had drifted from `marv-curator`, so it could offer models the
  runtime no longer recommends (and the old `// t3, vision` tag column). It now
  reads the same catalog the `marv-mlx` runtime uses
  (`~/.cache/marv/mlx/curated-llms.md`), showing each model id with its tagline,
  and falls back to a bundled list (refreshed to match `marv-curator`) when that
  file is absent. The list is also taller so entries are not clipped.

### Changed

- **Menus replaced modal windows in the TUI.** The session picker (load/fork/
  tree), model/thinking menu, context viewer, download picker, and extension
  prompt/confirm/select/presented panels now render inline at the input line,
  between the two rules around the prompt, instead of floating windows over the
  chat. Picking an item applies it and drops straight back into input mode;
  `Esc` cancels (in the thinking submenu it steps back a level first, and during
  a download it cancels the download). The model menu now applies each choice
  immediately — model and thinking level are picked one at a time. Internal TUI
  modules were renamed to match: `session_modal.py` → `session_panels.py`,
  `model_modal.py` → `model_panel.py`, `context_modal.py` → `context_panel.py`,
  `download_modal.py` → `download_panel.py`, with a shared `panel.py` base.

- **Aligned with the `marv-mlx` runtime.** Naming/contract alignment with the
  renamed runtime (was `ymlx`) — the MARV family is now harness `marv` + runtime
  `marv-mlx`. No harness identity change (repo, package, binary, config and state
  paths are unchanged):
  - env: `MARV_MLX_BASE_URL` → `AGENT_MLX_BASE_URL` and
    `MARV_MLX_MAX_OUTPUT_TOKENS` → `AGENT_MLX_MAX_OUTPUT_TOKENS`. `MARV_MLX_*` is
    the runtime's namespace; the harness keeps `AGENT_*`. The old names are read
    for one release with a deprecation warning.
  - `marv mlx …` (new): thin passthrough that `exec`s the `marv-mlx` CLI, with an
    install hint when it is absent.
  - the `marv-mlx` provider id and `MarvMlxProvider` are unchanged, so existing
    `state.toml`/configs/sessions keep working.
- **Seam B (opt-in): delegate local-server lifecycle to the `marv-mlx` runtime.**
  New `server_manager` config (`embedded` default | `marv-mlx`), env
  `AGENT_SERVER_MANAGER`, and `marv run --server-manager marv-mlx`. When set,
  model listing/start/stop go through the `marv-mlx` CLI (`llm/marv_mlx_cli.py`),
  falling back to the embedded manager on any failure or missing binary. The
  default path is unchanged.
- Docs now frame the two layers and link the runtime repo (`README.md`,
  `docs/llm.md`, `docs/configuration.md`).

## [0.107.0] - 2026-10-05

### Fixed

- **Multi-line prompt input.** The prompt is now a real text editor: it grows
  upward as you type, `shift+enter` (or `alt+enter`) inserts a newline, and
  `enter` submits. Up/down still walk suggestions and prompt history while the
  prompt is a single line, and move the caret once it is multi-line.
- **Suggestions run on a single Enter or click.** Pressing Enter (or clicking
  an entry) on a highlighted suggestion now applies *and* runs it — `/q` +
  Enter is enough; Tab remains apply-only.
- **Inline model picker.** Typing `/model` lists models in the dropdown above
  the input line; typing further filters them (`/model gpt-5` + Enter switches
  directly). Enter with no filter still opens the full picker, which keeps the
  thinking-level settings.
- **Modals float over the TUI.** Modal screens no longer paint a dimming
  scrim, so the chat and status bar stay visible behind the model/session
  windows instead of being blanked out.
- **Click anywhere to type.** Clicking anywhere in the main window moves the
  caret into the prompt input; inside modals clicks are left alone.
- **Copy assistant replies.** `ctrl+o` copies the most recent reply to the
  clipboard (via OSC 52), and clicking an assistant message copies that
  message. The mouse-based terminal selection the TUI previously swallowed is
  therefore no longer the only way to reuse generated text.
- **Status bar no longer wraps into the input.** The footer is pinned to one
  row; an over-long left section ellipsizes instead of wrapping, and the
  working directory is abbreviated (`~` for home).
- **Accurate RAM figure.** The status bar now reads the physical memory
  footprint (`top -l 1 -stats pid,mem`, the same accounting Activity Monitor
  uses) for marv and its model server instead of `ps` RSS. RSS misses the
  GPU-wired Metal memory where MLX model weights live, which is why a loaded
  model showed as e.g. `681M` while the real footprint was ~5.8G. Falls back
  to `ps` RSS when `top` is unavailable.
- The startup hint "ctrl+o to expand tool output" was stale (tool rows have
  always toggled by click); it now advertises the actual bindings.

## [0.106.0] - 2026-10-04

### Added

- **Terminal-native theming.** The TUI no longer hard-codes a Nord-ish
  palette. `theme = "auto"` (the default) probes the terminal background and
  applies Textual's built-in `ansi-dark`/`ansi-light` theme, so marv matches
  the terminal it runs in. Any built-in Textual theme name (`nord`,
  `tokyo-night`, `dracula`, …) works too, `minimal` keeps the old palette, and
  an unknown name falls back to `auto` with a warning. Set it in config, with
  `AGENT_THEME`, or per run with `marv run --theme <name>`.
- `src/marv/tui/theme.py` — theme resolution and startup appearance detection
  (bounded OSC-11 probe, then the system appearance, then dark).
- **TUI snapshot guardrails.** The quiet layout and its resolved styles are now
  pinned by committed screen snapshots (`tests/delivery/tui/snapshots/`), so a
  CSS or theme regression shows up as a reviewable diff instead of slipping
  through. Regenerate with `MARV_UPDATE_SNAPSHOTS=1 make test`.

### Changed

- **TUI redesign: quiet, rule-based layout.** Filled panels and per-message
  borders are gone. Tool calls render as a single `✓ Reading  src/parser.py`
  row (`✓` success / `✕` error / spinner while running) with the output
  indented beneath; thinking is a dim italic `▾ Thought` row; the input sits
  between two rules; the startup header is a compact two-line summary instead
  of the ASCII-art banner.
- **Textual 6.6 → 8.2** (the TUI toolkit). Textual 8 ships the built-in
  `ansi-dark`/`ansi-light` themes and the newer CSS properties
  (`text-opacity`, `text-overflow`) the redesign needs.
- Tool results now carry `is_error`, so the tool row can show an honest `✕`
  instead of inferring failure from the result text.

### Fixed

- **Presented extension views no longer crash on close.** `Screen.dismiss()` is
  synchronous as of Textual 8, so a queued refresh tick or a trailing key event
  could pop the base screen and raise `ScreenStackError`. The modal now
  dismisses at most once.

## [0.105.1] - 2026-10-04

### Fixed

- **Local MLX runs no longer die silently under memory pressure.** The
  `marv-mlx` backend now caps the output budget it asks the local server for
  (2048 by default, matching ymlx's default, overridable with
  `MARV_MLX_MAX_OUTPUT_TOKENS`). A large configured `max_output_tokens` raised
  peak prefill allocation to the point where the Metal working-set limit could
  be exceeded mid-response on unified-memory Macs.
- **In-stream server errors are surfaced instead of swallowed.** The
  OpenAI-compatible transport now raises on a top-level `error` payload in the
  SSE body (how the mlx_vlm server reports a Metal OOM after HTTP 200) and
  treats a stream that ends without a finish reason or `[DONE]` as an error.
  Previously both looked like a successful, empty completion, so the turn ended
  with nothing shown and no retry attempted.

## [0.105.0] - 2026-10-04

### Added

- **Model downloads are explicit and visible.** When the remembered/configured
  MLX model is not in the local Hugging Face hub, marv no longer starts
  fetching it silently in the background. The TUI opens a download picker,
  offering the curated suggestions for this Mac's RAM tier plus any Hugging
  Face id, and shows live progress (bytes, percentage, progress bar) while
  downloading. Cancelling leaves the previous model untouched.
- The TUI model picker (`/model`) now downloads a model that is not present
  locally before switching to it, using the same progress modal.
- `marv run --headless` downloads a missing model too, reporting progress on
  stderr instead of stalling invisibly; a failed download exits non-zero.
- `src/marv/llm/model_download.py` — a download worker (`ModelDownload`) that
  runs `huggingface_hub.snapshot_download` in the `mlx-vlm` interpreter and
  streams JSON progress back to marv.

## [0.104.0] - 2026-10-04

### Added

- The TUI status bar now shows host memory usage: RAM held by marv and its
  model server, installed RAM, and memory currently available. The model
  server is counted even when it was started outside marv (read from the port
  listener).
- The TUI status bar shows a live generation speed in tokens per second,
  estimated from the streamed output and smoothed across samples.

## [0.103.0] - 2025-10-04

### Added

- **Tool approval mode** (`approval_mode` / `AGENT_APPROVAL` / `marv run --approval`).
  `destructive` asks before `write`/`edit` and risky shell commands; `all` asks
  before every tool call. Off by default. In the TUI it uses a Yes/No confirmation;
  headless denies an unapproved tool with a message.
- `marv --version`.
- A `Release` workflow that verifies `VERSION` against the tag, builds the
  wheel/sdist, smoke-tests the install, and attaches artifacts to the release.
- A `build` CI job that builds and smoke-tests the wheel on every push/PR.
- `CONTRIBUTING.md`, `SECURITY.md`, issue/PR templates, and Dependabot config.

### Changed

- Filled out `pyproject.toml` metadata (license, authors, keywords, classifiers,
  project URLs).
- Fixed stale documentation: old `agent.*` module names, `uv run agent run`
  examples, example config paths, and a sample session provider. `PLAN.md` is now
  marked as an archived historical document.

## [0.102.0] - 2025-10-04

### Changed

- Default branch is now `main` (was `master`), so the CI workflow in
  `.github/workflows/` runs on push and pull request as intended.

## [0.101.0] - 2025-10-04

First tagged release.

### Added

- **Last-used selection is remembered across restarts.** The active
  provider/model/thinking level is persisted to `~/.cache/marv/state.toml` and
  reapplied on the next start, so the model picker no longer blocks startup.
  Explicit config files, `AGENT_*` env vars, and CLI flags still take
  precedence over the remembered value.
- **The model server starts on launch.** When a model is already selected
  (remembered, from config, or resumed), marv warms its local server
  immediately instead of waiting for the first prompt.
- `VERSION` file as the single version source, wired into the package metadata
  via hatchling.

### Notes

- Local-first backends only: `marv-mlx` (default) and `apple-fm`.
- **Latency figures were prompt-only in 0.110.0.** The transport reported
  `prompt_tokens` and `schema_tokens`, but `Agent` discarded the schema count,
  so the TUI `ttft <ms>/<N>t` figure and the bench `prefill (median)` line
  omitted tool schemas. Fixed in 0.111.0: `Agent`
  threads schema tokens through, and `marv bench` reports them.

[0.111.0]: https://github.com/pavsefcik/marv/releases/tag/v0.111.0
[0.110.0]: https://github.com/pavsefcik/marv/releases/tag/v0.110.0
[0.109.0]: https://github.com/pavsefcik/marv/releases/tag/v0.109.0
[0.108.0]: https://github.com/pavsefcik/marv/releases/tag/v0.108.0
[0.107.0]: https://github.com/pavsefcik/marv/releases/tag/v0.107.0
[0.106.0]: https://github.com/pavsefcik/marv/releases/tag/v0.106.0
[0.105.1]: https://github.com/pavsefcik/marv/releases/tag/v0.105.1
[0.105.0]: https://github.com/pavsefcik/marv/releases/tag/v0.105.0
[0.104.0]: https://github.com/pavsefcik/marv/releases/tag/v0.104.0
[0.103.0]: https://github.com/pavsefcik/marv/releases/tag/v0.103.0
[0.102.0]: https://github.com/pavsefcik/marv/releases/tag/v0.102.0
[0.101.0]: https://github.com/pavsefcik/marv/releases/tag/v0.101.0
