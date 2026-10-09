# marv — Roadmap / next work

Candidate work, roughly ordered by value. Sizes are rough: **S** < 1h,
**M** ~ half a day, **L** > a day. Nothing here is committed scope — it is a
menu, not a promise.

Current state: `v0.109.0` (plus uncommitted P0 latency work pending release).
`VERSION` is authoritative — this line is a snapshot. See
[`CHANGELOG.md`](CHANGELOG.md) for what shipped, and
[`docs/coworking-plan.md`](docs/coworking-plan.md) for the local-LLM
coworking direction (its P0 latency work is shipped; P1–P5 remain).

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

## P1 — Predictable wait: tell the user how long before they wait

> **Status: shipped (items 1–3, 5, and the `say why` + `/compact` half of 4).**
> `LatencyFit` calibration, the pre-request `WaitEstimate`, the TUI narration,
> the latency tier, and on-demand `/compact` are implemented and tested. The
> *schema-light retry* half of item 4 is **not** done — it needs the
> coworking-plan model router (P2.1), which does not exist yet; today the
> schema-dominated case names the cost honestly instead of offering a retry
> that is not wired.

A local model's first token can be two minutes away on a large prompt, and
`mlx_vlm` sends nothing while it prefills. With the prefill read timeout now
removed (so we no longer kill the wait), the raw experience is *silence with no
end in sight*: the user cannot tell "still prefilling" from "wedged", and has no
signal to decide whether to wait, cancel, or `/compact`. The fix is not to make
prefill faster (that is [`docs/coworking-plan.md`](docs/coworking-plan.md) P1)
but to **make the wait legible**: predict it, narrate it while it happens, and
offer the ways out when it is going to be long.

This is deliberately ordered **calibrate → predict → narrate → advise**, so
every number shown to a user is derived from that machine's own measurements
rather than a hardcoded constant.

### 1. Per-model prefill/decode calibration *(S–M)*
Learn the two constants the prediction needs from turns we already measure.
`TTFT ≈ prefill_seconds + decode_seconds`, and both terms are linear in a token
count, so a handful of samples are enough for a least-squares fit:
`prefill_tok_per_s` from `prompt_tokens`/`ttft_ms`, and decode `tok/s` from the
existing `TokenSpeedTracker`. Persist the fit per model in `state.toml` beside
the last-used selection, refresh it opportunistically after each turn, and seed
it from `marv bench` when available. Handle the two regimes explicitly: a
*cold* model start (weights loading, tens of seconds — a separate constant) and
a *cache hit* (a server-side prefix cache makes a large prompt cheap, so a
sample that is far below the fit must not be treated as noise).
- Where: `src/marv/llm/latency.py` (fit helpers), `src/marv/runtime/agent.py`
  (`note_request`/`note_first_token` already bracket the interval
  exactly), `src/marv/config/state.py`, `src/marv/cli/bench.py`
- Effort: S–M · Risk: low (pure, offline fit; worst case the ETA is scruffy)

### 2. Predict the wait before it starts *(S)*

Compute the estimate when the request is built, not after the first token.
`Agent._agent_loop` already knows the exact prompt size from
`count_messages_tokens(messages_for_llm)` at `note_request`, and the transport
knows the schema size; the missing piece is that `Agent` currently threads only
`prompt_tokens` and **drops `schema_tokens`** (noted in the coworking audit),
so tool schemas are invisible to the estimate. Thread both through, then emit a
prediction on the same `assistant_metadata` channel as an `eta_ms` the delivery
layer can render.
- Where: `src/marv/runtime/agent.py` (`_apply_provider_ttft`/`note_request`),
  `src/marv/llm/openai_compat.py` (`_latency_metadata`, `schema_tokens`),
  `src/marv/llm/latency.py`
- Effort: S · Risk: low

### 3. Narrate the prefill in the waiting indicator *(S–M)*

The `WaitingIndicator` currently spins with no sense of progress. Give it the
prediction and an elapsed clock: `prefilling 32k tokens · ~2:00 · 1:14 elapsed`
with a live/cancellable affordance. Percent is *derived from elapsed vs the
estimate*, and must be presented as an estimate that revises itself rather than
a progress bar that jumps or stalls at 99% — a wrong-but-honest ETA builds more
trust than a frozen one. When the estimate is exceeded, switch to a different
message ("taking longer than predicted") instead of reaching 100% and lying.
Also add an elapsed timer to the generating phase so decode time is visible too.
- Where: `src/marv/tui/chat.py` (`WaitingIndicator`),
  `src/marv/tui/renderer.py`, `src/marv/tui/status.py`
- Effort: S–M · Risk: low (display only; no protocol change)

### 4. Turn the prediction into an exit offer *(M)*

When the predicted TTFT crosses a threshold, the wait is a decision, not just a
fact — surface the options where the user already looks:

- **`/compact` before sending.** If the estimate is dominated by prompt size,
  offer to compact first and re-estimate, using the existing manual-compaction
  work (ROADMAP P2 "/compact on demand").
- **Drop avoidable cost.** Tool schemas are on the wire for every turn; a
  chat-shaped turn predicted to be slow is a signal to use the coworking-plan
  router (P2.1) to re-send without schemas and shave TTFT.
- **Say why.** Attribute the wait to prompt vs schemas vs cold start, reusing
  the `prompt_tokens`/`schema_tokens` split, so the advice is specific rather
  than "your context is long".
- Where: `src/marv/tui/controller.py`, `src/marv/runtime/context.py`,
  `src/marv/runtime/agent.py`
- Effort: M · Risk: medium (must not nag every turn or fight the model router)

### 5. Latency tier in model choice *(S)*
Surface the calibrated TTFT/tok/s per curated model in the model menu and `marv
bench`, so a slow-but-smart model is chosen knowingly rather than discovered
mid-session. Overlaps coworking-plan P5.3; the calibration in (1) is what makes
it honest.
- Where: `src/marv/tui/model_panel.py`, `src/marv/llm/model_download.py`
- Effort: S · Risk: low

**Acceptance:** on a machine with a few recorded turns, sending a 30k-token
prompt shows an ETA within ~25% of the eventual TTFT, the waiting indicator
tracks elapsed against it, and a predicted-long turn offers `/compact` / a
schema-light retry that measurably lowers the estimate. On a first-ever run with
no history, the UI degrades to elapsed-only (no fabricated ETA).

**Explicitly not this:** a spinner that implies progress we do not have, a
hardcoded "this takes ~2 min" constant, or a timeout that kills a slow prefill.
The prediction is advisory; the wait itself stays uncapped.

---

## P2 — Session and context UX

### Session titles + searchable session browser
Sessions are flat JSONL files with no title, so `/load` and `marv sessions` are a
list of timestamps. Auto-title each session from its first user message (or a
cheap summary) and show it in the picker; add search/filter once there are dozens.
- Where: `src/marv/runtime/session.py` (`SessionMetadata`), `src/marv/tui/session_panels.py`,
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
(`src/marv/llm/model_download.py`, `src/marv/tui/download_panel.py`), with
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
- Where: `src/marv/tui/app.py`, `src/marv/tui/model_panel.py`
- Effort: S · Risk: low

### Theme config
Completed — themes are configurable via `theme`, `AGENT_THEME`, and
`marv run --theme`, with `auto` following the terminal. See `docs/tui.md`.

Appearance is detected once at startup. It is deliberately not re-probed
mid-session: Textual's input parser has no OSC 11 handler, so an unsolicited
reply arrives as key input. Following a live light/dark switch would need a
supported change signal (e.g. text-driven DEC mode 2031) from Textual.

### Incremental streaming render
`MessageWidget.append_text` re-parses the *entire* accumulated Markdown on every
token (`Markdown(self._content)` per delta), which is quadratic: a 4000-delta /
20k-char answer spends ~7s in pure re-parse. Only the tail of a streaming
message changes, so render incrementally (re-parse a paragraph on its blank-line
boundary, or style the in-flight tail as plain text and re-render once the block
settles). Also consider coalescing deltas on a short timer so bursty providers
do not re-render per token.
- Where: `src/marv/tui/chat.py` (`MessageWidget`), `src/marv/tui/renderer.py`
- Effort: M · Risk: medium (Markdown correctness at block boundaries)

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
  TUI confirm panel and headless deny.
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
- Prefill waits are no longer killed: local-server backends have no read timeout
  by default, a pre-token read timeout is surfaced without retrying, and
  `AGENT_LLM_READ_TIMEOUT` sets a cap.
- `--read-only` mode (active tool set narrowed to read/grep/find/ls) and
  `marv doctor` for self-diagnosing setup failures.
- Prefill waits are *legible*: per-model prefill/cold-start calibration, a
  pre-request wait estimate, the TUI narration with an exit offer, and on-demand
  `/compact` — see P1 "Predictable wait" above.
