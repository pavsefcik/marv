# Changelog

All notable changes to marv are documented here. The version in
[`VERSION`](VERSION) is the single source of truth.

## [Unreleased]

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

[0.106.0]: https://github.com/pavsefcik/marv/releases/tag/v0.106.0
[0.104.0]: https://github.com/pavsefcik/marv/releases/tag/v0.104.0
[0.103.0]: https://github.com/pavsefcik/marv/releases/tag/v0.103.0
[0.102.0]: https://github.com/pavsefcik/marv/releases/tag/v0.102.0
[0.101.0]: https://github.com/pavsefcik/marv/releases/tag/v0.101.0
