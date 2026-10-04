# marv — Roadmap / next work

Candidate work, roughly ordered by value. Sizes are rough: **S** < 1h,
**M** ~ half a day, **L** > a day. Nothing here is committed scope — it is a
menu, not a promise.

Current state: `v0.106.0`. See [`CHANGELOG.md`](CHANGELOG.md) for what shipped.

---

## P1 — Safety and robustness

### Sandbox / read-only mode
`approval_mode` is a heuristic guardrail, not isolation. Add a first-class
`--read-only` mode that narrows the active tool set to `read`/`grep`/`find`/`ls`
(useful for "explain this repo" runs), and optionally a workspace-root
confinement for `write`/`edit`. Today the only path guards are the opt-in
extension examples (`examples/extensions/protected-paths.py`).
- Where: `src/marv/tools/registry.py`, `src/marv/runtime/agent.py`,
  `src/marv/config/runtime.py`, `src/marv/cli/__init__.py`
- Effort: M · Risk: medium (touches tool activation)

### `marv doctor`
One command that checks the resolved config, the HF hub, model presence, server
reachability, and whether configured extensions load. Makes setup problems
self-diagnosing instead of surfacing as a cryptic TUI error.
- Where: `src/marv/cli/__init__.py`, `src/marv/llm/local_server.py`
- Effort: S · Risk: low

---

## P2 — Session and context UX

### Session titles + searchable session browser
Sessions are flat JSONL files with no title, so `/load` and `marv sessions` are a
list of timestamps. Auto-title each session from its first user message (or a
cheap summary) and show it in the picker; add search/filter once there are dozens.
- Where: `src/marv/runtime/session.py` (`SessionMetadata`), `src/marv/tui/session_modal.py`,
  `src/marv/cli/sessions.py`
- Effort: M · Risk: low

### `/compact` on demand + context pressure indicator
Compaction exists but is automatic. Expose a manual `/compact` command and make
the status-bar token figure show pressure (it already shows `tokens/max (%)`).
- Where: `src/marv/runtime/context.py`, `src/marv/tui/controller.py`,
  `src/marv/tui/status.py`
- Effort: S–M · Risk: low

### Session export
`/export` (and `marv export`) to write a session as Markdown or JSON for sharing
or archiving.
- Where: `src/marv/runtime/session.py`, `src/marv/cli/sessions.py`
- Effort: S · Risk: low

---

## P2 — Model and server management

### In-app model management
Downloading a missing model in the TUI and in headless runs now works
(`src/marv/llm/model_download.py`, `src/marv/tui/download_modal.py`), with
curated suggestions per RAM tier. Still missing: a `marv models` command
(list / download / delete / show size) so marv is self-sufficient outside the
TUI, and delete/size accounting.
- Where: `src/marv/llm/model_download.py`, `src/marv/llm/mlx_models.py`,
  `src/marv/cli/__init__.py`
- Effort: M · Risk: medium (HF hub interaction)

### Server swap UX
Make model switching explicit: a clear "swapping model…" progress state, and an
optional unload-on-idle so a big model doesn't hold RAM forever. Complements the
new RAM/speed status readout.
- Where: `src/marv/llm/local_server.py`, `src/marv/tui/app.py`
- Effort: M · Risk: medium

---

## P3 — Quality and process

### Branch protection on `main`
Require the `Test` workflow and a review before merging. Currently `main` is
unprotected (the CI workflow exists and passes; nothing enforces it).
- Where: GitHub repo settings (not code)
- Effort: S · Risk: low

### `pre-commit` config
Wrap `ruff check` / `ruff format` / `mypy` so issues are caught before CI.
- Where: new `.pre-commit-config.yaml`
- Effort: S · Risk: low

### More CLI end-to-end tests
Cover the precedence seam: `marv run -m X` must override a remembered model from
`state.toml`, and `--provider` must win over config. The pieces are tested
individually; the full CLI path is not.
- Where: `tests/delivery/cli/`
- Effort: S · Risk: low

### Dependabot backlog
Several dependency PRs are open (textual, mypy, pytest, tiktoken, types-pyyaml,
actions/checkout). Worth a batch review/merge once green.
- Effort: S · Risk: low

---

## P3 — Delivery and interface

### Headless JSON output mode
`marv run --headless --json` emitting structured events (text, tool calls,
results, usage) so marv can be scripted and driven from CI or other tools.
- Where: `src/marv/cli/headless.py`
- Effort: M · Risk: low

### Multi-line prompt / `$EDITOR` integration
Long prompts in a one-line input are painful. Add a keybinding to open the current
buffer in `$EDITOR` and paste it back.
- Where: `src/marv/tui/input.py`
- Effort: M · Risk: medium (terminal handoff)

### Opt-in model picker
We ship auto-apply of the last-used model (Option B). Add a config flag to instead
open the picker pre-selected (Option A: press Enter to confirm), for people who
want the explicit "this is what I'm about to load" moment.
- Where: `src/marv/tui/app.py`, `src/marv/tui/model_modal.py`
- Effort: S · Risk: low

### Theme config
Completed — themes are configurable via `theme`, `AGENT_THEME`, and
`marv run --theme`, with `auto` following the terminal. See `docs/tui.md`.

Appearance is detected once at startup. It is deliberately not re-probed
mid-session: Textual's input parser has no OSC 11 handler, so an unsolicited
reply arrives as key input. Following a live light/dark switch would need a
supported change signal (e.g. text-driven DEC mode 2031) from Textual.

### Tool-call diffs
Editing tools show their raw result text. Render `edit`/`write` results as a
syntax-highlighted diff with add/remove bands (and horizontal scroll for long
lines), like other modern terminal agents.
- Where: `src/marv/tui/chat.py` (a new diff view), `src/marv/tools/edit.py`
- Effort: L · Risk: medium

### Elapsed-time and cost in the footer
The footer shows tok/s but not how long a turn has taken or what it cost.
Add a live elapsed timer to the waiting indicator and a turn cost estimate for
providers that report pricing.
- Where: `src/marv/tui/chat.py`, `src/marv/tui/status.py`
- Effort: M · Risk: low

---

## P3 — Extensions and docs

### Extension authoring walkthrough
`docs/extensions.md` documents the API, but there is no step-by-step "write your
first extension" tutorial. The examples are good reference material.
- Where: `docs/extensions.md` (or a new `docs/extension-tutorial.md`)
- Effort: S · Risk: low

---

## Recently completed (do not re-suggest)

- Opt-in tool approval (`approval_mode`, `AGENT_APPROVAL`, `--approval`) with a
  TUI confirm modal and headless deny.
- `marv --version`; `VERSION` as the single version source.
- Release workflow (VERSION/tag check, build, smoke-test, attach assets) and a
  build+smoke-test CI job.
- `CONTRIBUTING.md`, `SECURITY.md`, issue/PR templates, Dependabot.
- `pyproject.toml` metadata (license, authors, keywords, classifiers, URLs).
- Docs drift fixes; `PLAN.md` archived.
- Last-used model/thinking persistence + model server auto-start on launch.
- Visible, opt-in model downloads: a missing remembered model opens the download
  picker instead of downloading silently; the picker shows live progress and
  offers curated per-RAM-tier suggestions.
- Status bar shows model RAM usage and generation speed.
- Terminal-native theming (`auto` follows the terminal background) with a quiet,
  rule-based TUI layout, plus screen snapshots guarding it against regressions.
