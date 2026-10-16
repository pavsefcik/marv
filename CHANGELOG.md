# Changelog

All notable changes to marv are documented here. The version in
[`VERSION`](VERSION) is the single source of truth.

## [Unreleased]

## [1.122.0] - 2026-10-16

### Added

- **Multimodal input.** Send an image (or page 1 of a PDF) to a vision-capable
  model. `Message.content` stays a string; images ride in a new
  `Message.attachments` field of `ImageAttachment`s, and `to_api_dict()` emits
  OpenAI content parts (`text` + `image_url` data URLs) for a user message that
  carries them. The provider protocol gains `supports_vision()`
  (`OpenAICompatibleProvider`/`marv-mlx`: true — `mlx_vlm` is vision-capable;
  `apple-fm`: false), and a text-only backend still runs the turn, dropping the
  images with a system warning. `count_messages_tokens` charges a per-image
  `IMAGE_TOKEN_COST` budget rather than tokenizing the base64 payload.

- **`@path` attachments in the TUI.** Typing `@path` (or `@"path with spaces"`)
  attaches an image or PDF page, strips the token from the prompt, and shows a
  dim `[image: name]` line that is restored when a session is replayed. Anything
  that is not an existing image or PDF stays as ordinary text. Loading runs off
  the UI thread; a failure aborts the turn with a system message.

- **`--attach` in headless mode.** A repeatable `marv run --headless --attach
  PATH` flag, plus `@path` tokens found in the prompt, send the same attachments
  through `run_headless` → `Agent.run`.

- **PDF page-1 rendering** (`src/marv/runtime/attachments.py`) via `pdftoppm`
  (poppler) or macOS `sips`, with a clear "install poppler" error when neither
  is available. An attachment over 8 MiB is rejected with a resize hint. No new
  Python dependency.

- **PDF page selection.** `@report.pdf:3` attaches one page and
  `@report.pdf:2-4` attaches a range, in the TUI and in the repeatable
  `marv run --attach` flag. Chosen pages render with `pdftoppm`, one attachment
  per page (named `report.pdf p.3`), up to 10 per attachment. Requesting any
  page beyond 1 without `pdftoppm` fails with the `brew install poppler` hint
  instead of silently sending page 1; the macOS `sips` fallback still renders
  page 1.

- **`pdf` tool.** One tool that does real PDF work by driving external engines
  instead of adding a Python dependency: `info` and `text` (poppler), `render`
  (`pdftoppm`), `compress` (Ghostscript), and `merge`, `split`, `rotate`
  (qpdf). A missing engine fails with the binary and the exact install command
  (`` `pdftoppm` not found. Install poppler: brew install poppler ``).
  Writing actions never overwrite the input, default to a sibling file, verify
  the engine really wrote its output, and report the produced files as
  path + size. Under `approval_mode=destructive` every writing action needs
  approval; `info` does not, and `text` needs it only when it writes.

- **`marv doctor`: `pdf tooling`.** Reports which of the five engines the `pdf`
  tool drives are present, with their paths, and which are missing, with the
  Homebrew command that installs them. `ok` when all are present, `warn`
  otherwise — PDF work is optional, so a bare machine is not a failure.

- **`docs/tools.md`.** Documents the `pdf` schema, the command each action runs,
  and the rules the tool enforces.

- **`office-files` now ships with marv.** The office playbook moved from
  `examples/skills/office-files/` into the package at
  `src/marv/skills/builtin/office-files/SKILL.md`, and the default skill loader
  always searches the packaged `builtin/` directory. A fresh `Agent` therefore
  sees `office-files` with no setup — `$office-files` works on first launch,
  with no copy into `~/.marv/skills` and no `skills_dirs` entry.

- **Office/document direction.** `ROADMAP.md` sharpens the coworking track
  around the office-assistant framing (documents and images first, repo editing
  as one artifact type) and gains a near-term "Office capabilities" list with
  sizes and gates: batch image resize/convert with the built-in `sips`,
  PDF rasterise/compress, PDF vector ops (all three gated on the PDF-engine
  decision), plus pointers to the existing multimodal-input (C4.1) and
  artifacts-as-output (C4.3) items.

- **`docs/office.md`.** Office positioning, the recipes that work today with the
  binaries macOS already ships (`sips`, `qlmanage`, `textutil`, `mdls`) through
  the existing `bash` tool, and an honest account of what is missing — `sips`
  and `qlmanage` render **page 1 only**, there is no built-in PDF compressor or
  page count — with the dependency decision the PDF items need.

- **`office-files` skill** (`src/marv/skills/builtin/office-files/SKILL.md`). A
  playbook for office file work: discover available binaries
  first, batch image resize/convert with `sips`, PDF → images preferring
  `pdftoppm` and stating the single-page `sips` fallback honestly, PDF
  compression via `gs`/`qpdf` or an explicit "install one" message, and safety
  rules (never overwrite inputs, quote paths, verify outputs, report what ran).
  No new runtime tool, dependency, or config key.

- **`@path` completion in the prompt.** A trailing `@partial` (or `@"partial`)
  opens the dropdown the `/` commands and `$` skills already use, listing
  matching entries from the working directory: directories first with a trailing
  `/`, names holding whitespace quoted as `@"my shot.png"`, and dotfiles only
  once the partial starts with a dot. Each choice writes the whole prompt back
  (the text before the `@`, the path, a trailing space) so a mention in the
  middle of a sentence stays intact. The list is paths, not attachments — only an
  existing image or PDF is attached on submit.

- **Tab follows the option id.** `Tab` now rewrites the prompt from `Option.id`
  when an option carries one, as `Enter` and a click already did, so a model
  choice or a completed path behaves the same on either key. `/` and `$`
  options carry no id, so their behaviour is unchanged.

- **`marv help` (`marv -h` / `marv --help`).** A curated help screen in the
  marv-mlx style: the tagline, one usage line per command, and how to reach the
  full option lists. All three spellings print exactly the same text; the root
  group's built-in help option is replaced by an eager `-h`/`--help` flag, so
  `marv run --help` and friends keep their full Typer option help.

- **`marv version`.** Prints `marv <version>`, matching the existing eager
  `marv --version`.

- **`!command` in the TUI.** A submitted prompt starting with `!` runs the rest
  through the agent's `bash` tool and prints the output in the chat instead of
  sending it to the model. It is an explicit user action, so it skips the
  approval policy; `!` alone does nothing. `/help` lists it.

- **`--attach` in the interactive TUI.** The repeatable `--attach PATH` flag was
  honored headless only; a TUI launched with it now attaches those images (and
  page-1 PDFs) to the first submitted message, ahead of any `@path` tokens, then
  clears them. The no-prompt path keeps the same validation and error as
  headless.

- **`marv doctor` reports the update check.** The verdict says whether
  `update_check` is on and compares the cached latest release with the running
  version — reading the cache only, so doctor never touches GitHub. `warn` when
  a newer release is cached, `ok` when up to date or disabled.

- The wait prediction reads live state from the serving mlx_vlm server
  (`/v1/metrics`, via the new `marv.llm.server_metrics`). Server-measured
  turns seed the prefill-rate calibration before marv has any samples of its
  own, so a first-ever run gets a real ETA instead of none; a persisted
  calibration is never overwritten.

- `WaitEstimate` now carries `cached_tokens`: when the server reports prefix
  caching enabled, the prediction prices only the tokens added since the
  previous turn instead of the full prompt, and the TUI wait narration shows
  the cached share ("prefilling 1.2k tokens (9.8k cached)"). Compaction and
  model switches reset the expected prefix.

### Changed

- **Attachments are stored on disk, not in the session.** `ImageAttachment.data`
  (base64) is excluded from serialization; each blob is written content-addressed
  by sha256 under the session's `attachments/` directory and referenced by a
  `ref` filename. A session JSONL no longer carries megabytes of image bytes,
  and a resumed session reads the blob back before the turn. Identical bytes
  deduplicate to one file, and a missing blob degrades to no image rather than
  failing the turn.

- **`SkillSource.BUILTIN` is the lowest-priority skill source.** The discovery
  order is now builtin → user (`~/.marv/skills`) → config (`skills_dirs`) →
  project (`.marv/skills`), so a user, config, or project skill with the same
  name overrides the packaged one and the override is recorded as a collision in
  the loader diagnostics.

- **Large image attachments are downscaled at attach time.** An image over
  1.5 MB is scaled to 1568 px on the long edge with macOS `sips` before it is
  base64-encoded, so a multi-megapixel photo no longer inflates the session
  JSONL and every turn's prefill with pixels the vision encoder discards.
  Best-effort: a machine without `sips` (or an engine failure) sends the
  original bytes, and the 8 MiB hard cap still applies to the result.

- **`@` completion lists only files that can be attached.** The dropdown now
  offers directories (for descent) and files whose suffix is an image or `pdf`
  — the same set the attachment parser accepts — instead of every entry in the
  directory, so a stray source file is no longer a suggestion. A partial that
  matches no attachable file leaves the dropdown hidden.

- **Vision support is detected per local MLX model.** `marv-mlx` no longer
  claims every model accepts images: `model_supports_vision` reads the local hub
  snapshot (image-processor config, a vision marker in `config.json`, or a
  VLM-shaped model name). A text-only MLX model now drops `@path` images with a
  system warning instead of failing at the server, and a model with no local
  snapshot stays permissive on purpose so an image is never silently dropped.
  `marv doctor` gains a `vision` check for the active provider and model.

- **Startup hint names the useful controls.** The header's second line now reads
  `Type /help for commands · escape interrupt · ctrl+l clear · ctrl+o copy reply`
  instead of advertising `shift+enter`, which the prompt placeholder already
  explains.

- **`/help` explains the commands.** It prints a block with one line per command
  (`/compact    summarise old context on demand`) and the keys bound outside the
  prompt, instead of one pipe-separated list.

- **Transient status rows lead with `✓`.** A settled notice reads `✓ model ready`
  rather than `-- model ready`; the `--` marker stays for permanent, historical
  system messages. The spinner form is unchanged.

### Fixed

- **A chosen theme is no longer wiped on the next start or model switch.** The
  TUI's `remember_selection` built a fresh state object and saved it, dropping
  the persisted theme (and every model's latency fit). It now merges through
  `marv.config.state.remember_selection`, so a restart applies the theme again.

- The `_proctitle` sitecustomize hook now chain-loads the next `sitecustomize`
  on `sys.path`. Python imports only the first one, and the hook directory sits
  on `PYTHONPATH` ahead of site-packages, so it silently shadowed the marv-mlx
  APC (prefix-cache) defaults installed in the mlx-vlm tool env — every
  marv-launched server ran with prefix caching off and re-prefilled the whole
  conversation every turn (`cached_tokens=0`, 8-13 s prefills on long contexts).
  Embedded launches now log `APC enabled` and reuse cached prefixes.

- **User prompts are marked by a stripe.** A prompt carries a `$primary` border
  down its left edge, so it cannot be mistaken for an answer. It is a border, not
  a background, because the ANSI themes' `$surface`/`$panel` are transparent.

- **The prompt placeholder is muted.** The placeholder rule never applied — a `>`
  combinator cannot match a Textual component class — so the selector is a
  descendant selector and the theme's disabled text colour now wins over
  TextArea's own default. Under the ANSI themes, where `$text-disabled` has no
  alpha and collapses to the terminal's default (white) foreground, an `:ansi`
  rule pins the placeholder to bright black so it is visibly grey there too.

- **Selecting part of an answer.** An answer rendered as a Rich `Markdown`
  renderable, which carries no content offsets, so Textual had no cell to anchor
  a selection to and any drag over a reply selected — and `Ctrl+C` copied — the
  whole reply. Answers now render through Textual's own `Markdown` widget, so
  dragging over part of an answer selects exactly the text swept over: one line,
  a wrapped paragraph, or a range across paragraphs. Prompts, attachment lines,
  and the answer's markdown styling (headings, lists, code, bold) are unchanged,
  and `Ctrl+O` remains the whole-reply fallback.

- **System-message text is escaped.** A system row (including `!command` shell
  output) is rendered literally now, so square brackets in command output can no
  longer be parsed as Rich markup or raise a markup error.

## [1.121.0] - 2026-10-14

marv-mlx shape, a proper goodbye, update checks, and a proper uninstall.

### Added

- **Startup update check (`src/marv/updates.py`).** The TUI probes the latest
  GitHub release in the background (once a day, cached at
  `~/.cache/marv/update_check.json`) and, when one is newer, offers to install
  it. Accepting reinstalls the release wheel with `uv tool install --force`.
  Offline/rate-limited probes are silent and never stall startup. Opt out with
  `update_check = false` or `AGENT_UPDATE_CHECK=off`.
- **`marv update`.** Manually check and install the latest release
  (`--check` reports only, `--force` reinstalls, `-y` skips the prompt).
- **`marv uninstall`.** A picker for the removable pieces — marv and
  its cache/config; the marv-mlx runtime, its cache, and the shell wiring it
  added to `~/.zshrc`; the `mlx-vlm` uv tool; and models in the
  shared Hugging Face hub — with sizes shown first and nothing selected by
  default. marv-mlx is discovered wherever it lives (`~/.marv-mlx`,
  `~/.local/share/marv-mlx`, the pi launcher/extension, or Homebrew). Uses
  `gum` in the marv-mlx style (magenta wordmark, `gum choose
  --no-limit`, `gum confirm`) when it is on PATH, and falls back to a plain
  numbered prompt otherwise. Scriptable with
  `--marv`/`--marv-mlx`/`--mlx-vlm`/`--models`/`--all`, `--dry-run`, and `-y`.
- **Shared-server guard (`src/marv/instance.py`).** Running instances register
  in `~/.cache/marv/instance.json`; a second TUI that selects a different model
  warns that starting it will restart the shared server instead of doing so
  silently.
- **Exit sign-off (`src/marv/cli/goodbye.py`).** Quitting the interactive TUI
  clears the screen and leaves a nerd block at the top, in the marv-mlx idiom:

  ```
  ▌▌ marv says bye!
     ✓ session saved        s-1a2b3c
     ✓ model unloaded       mlx-community/Qwen3.5-4B-MLX-4bit :11500
     ✓ memory released      5.2 GB
     all systems terminated · marv 1.121.0
  ```

  Every row is observed, not asserted: the memory figure is read from the model
  server's footprint (GPU-wired weights included) *before* the unload, a session
  with no server says `no model was running`, and a server that is somehow still
  answering says `model still running` (with the `marv-mlx stop` hint) and never
  prints `all systems terminated`. It runs only for a real terminal on a clean
  exit, so piped output and crashes are untouched.
- **`Config.goodbye` (`true`) and `AGENT_BYE`.** Turn the sign-off off with
  `goodbye = false` or `AGENT_BYE=off`.
- **`/theme` — an in-TUI theme chooser with live preview.** Highlighting an
  entry repaints the whole screen, Escape restores the palette that was active,
  and Enter remembers the choice in `state.toml` (weakest layer, so config/env/
  CLI still win). `/theme <name>` applies one directly. `auto` resolves from the
  system appearance rather than a fresh OSC 11 probe, which would be unsafe once
  Textual owns the terminal. The theme is now part of the remembered last-used
  selection.
- **`theme = "marv-mlx"` preset, now the default.** The marv family magenta
  (the `▌▌` wordmark, dim greys, green/yellow markers) is what marv starts in;
  `theme = "auto"` opts back into following the terminal background, and
  `minimal` keeps the Nord-ish palette.
- **`StoppedServer` / `LocalServerProvider.unload()`.** The exit path records
  which model it unloaded so the sign-off can name it; the CLI's safety-net
  stop no longer erases that record.

### Changed

- **TUI shaped like marv-mlx.** The startup wordmark is `▌▌ marv`, the prompt
  line carries a `▌` mark, failed tool rows use `✗` (was `✕`), and input-docked
  menus gained `───` separators before their escape hatches plus
  `↑/↓ nav · enter select · esc back` footers. The `marv-mlx` palette is now the
  default theme (`theme = "auto"` restores terminal-following).

## [1.120.1] - 2026-10-10

Dependabot off.

### Removed

- **`.github/dependabot.yml` removed.** Merging a Dependabot pull request makes
  GitHub stamp `Co-authored-by: dependabot[bot]` on the squash commit, and
  co-authors count as repository contributors, so `dependabot[bot]` kept
  reappearing in the contributors list even after the history was rewritten.
  Dependency bumps are now done by hand (`make deps`); Dependabot alerts and
  automated security updates were already disabled.

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
