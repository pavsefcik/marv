# marv — Roadmap / next work

Candidate work, roughly ordered by value. Sizes are rough: **S** < 1h,
**M** ~ half a day, **L** > a day. Nothing here is committed scope — it is a
menu, not a promise.

Current state: `v0.111.0`. `VERSION` is authoritative — this line is a snapshot.
See [`CHANGELOG.md`](CHANGELOG.md) for what shipped. The **coworking direction**
below is the long-range track (its measurement step and the harness-side
"predictable wait" work are shipped); the P2/P3 sections after it are the
harness backlog.

---

## Direction — local-LLM coworking

Status: **direction, not committed scope.** The measurement baseline (TTFT in
the TUI + `marv bench`) shipped in 0.110.0, and the harness-side predictable
wait (calibrate → predict → narrate → advise) shipped in 0.111.0. The steps
below remain open.

### The shift

Today marv is organised around one unit of work: *a repo in a working
directory, edited by a tool-calling coding agent*. The default persona
(`BASE_PROMPT`), the tool suite (`read`/`write`/`edit`/`bash`/`grep`/`find`/`ls`),
the approval defaults, and even the app name are all coding-shaped.

A coworking harness is organised around a different unit: *a conversation plus
artifacts* — a draft, a table, a set of files, a decision. The work is
read-mostly, interruption-heavy, and mostly questions. Code is one artifact
type among several (documents, images, data).

The consequence for architecture is not a rewrite. It is three changes of
emphasis:

1. **Latency becomes a correctness property, not a nicety.** A coworking
   partner you wait 40s for is not a coworking partner. Every design choice gets
   judged by time-to-first-token.
2. **Generation is planned before it starts, not steered during it.** Local
   models have coarse abort granularity; you must budget the turn up front.
3. **Input and output become multimodal.** Text-only messages and a
   text-only `to_api_dict` cannot support coworking.

Everything below is ordered so that **measurement precedes optimisation** and
**cheap wins precede new capability**.

### Latency budget: where the time actually goes

With the current architecture, a turn's wall time decomposes as:

```
TTFT  = prefill(all prompt tokens + all tool schemas)   <-- DOMINANT, and it grows
      + decode(first token)
      + transport/queue
```

Three structural facts in the current code make prefill the dominant term and
make it *grow* with the conversation:

- **The whole history is re-sent every turn.** `Agent._agent_loop` calls
  `self.provider.stream(messages_for_llm, ...)` each iteration (after hook
  preprocessing), over stateless HTTP. Unless the server has a persistent
  prefix cache, the full context is re-prefilled *on every turn* — TTFT rises
  roughly linearly with conversation length. This is the single largest latency
  cost in the product.
- **The system prompt contains a timestamp.** `_build_environment_section()`
  emits `datetime.now()` into the *first* message. It is stable within a turn,
  but `Agent.refresh_system_prompt()` re-stamps it whenever the tool set or an
  extension changes mid-session, and it always differs across launches,
  sessions, and resumes — so the prompt prefix defeats any cross-session prefix
  cache (shared persona + the project's `AGENTS.md`). A prefix-stable prompt is
  a prerequisite for caching, and the timestamp silently breaks it.
- **Tool schemas are always on the wire.** Every turn ships all tool schemas
  *and* repeats them as prose in `TOOL_DESCRIPTIONS`, even for "what does this
  paragraph mean?".

Secondary, smaller costs:

- **Rendering is per-token.** `MessageWidget.append_text` → `_update_content`
  runs `rich.Markdown(...)` + `update()` + `scroll_to_bottom()` on **every**
  delta, not per frame (`src/marv/tui/chat.py`). This is cheap relative to
  prefill but is pure waste and shows up as jank on long replies.
- **Session writes are synchronous** on the event loop: `Session.append` →
  `_append_message_entry` does a blocking `open()`/`write()` per entry
  (`src/marv/runtime/session.py`).
- **Thinking is irreversible once on.** `enable_thinking` is a request-level
  flag (`marv_mlx._build_payload`), and for Ministral Reasoning the trace is
  inherent to the model. You cannot disable reasoning mid-generation, so a turn
  that decides to "think" has already paid for it.

**Implication:** the highest-leverage work is *sending fewer prompt tokens per
turn* and *making the prefix cacheable*. Everything else is second-order until
that is measured.

### C1 — Kill prefill: cacheable prefix, smaller prompt *(M–L)*

1. **Prefix stability.** Move the volatile `Date/Time` out of the cached prefix:
   either drop it, reduce it to a date (no time), or place it *after* all static
   content. Add a regression test asserting the system prompt is byte-identical
   across two builds within the same day.
2. **Prefix warm-up.** After a session load or model start, send one cheap
   request containing only the static prefix (persona + `AGENTS.md` + skills
   list) so the server caches it before the user finishes typing. Gate this on
   evidence that the server actually caches; if it does not, skip it and pursue
   1.4 instead.
3. **Tool schema diet.** Send compact schemas, and gate the tool *set* per turn
   (see C2.1) so a chat turn ships no schemas at all. Remove the duplicate
   prose descriptions for tools the model already sees as schemas, or keep prose
   and drop nothing — but measure both.
4. **History trimming for the wire.** Keep full history in the session (truth),
   but send a *latency-trimmed* view: collapse old tool results (especially
   `read`/`grep`/`find` output) to short summaries, and reuse the existing
   `ContextManager` summarisation machinery. This is "lossy context for
   latency", distinct from compaction-for-space.
5. **Configurable prompt budget.** Add `prompt_sections` / per-section caps to
   `Config` + `AgentSettings`, so a workload can disable skills, or context
   files, or trim `AGENTS.md`.

**Acceptance:** a 10-turn conversation holds TTFT roughly flat instead of
climbing; a chat turn sends materially fewer prompt tokens than today.

### C2 — Plan the turn before it starts *(M–L)*

Because local generation cannot be steered mid-flight, the fast path must be
chosen up front.

1. **A heuristic turn router (no model call).** Before streaming, classify the
   resolved input into `chat` / `read` / `act` using signals that are already in
   the runtime: presence of a `/template`, `$skill`, an attached artifact,
   prior-turn tool use, and simple lexical cues (question vs imperative, file
   references). Map:

   - `chat` → no tools, tight prompt, thinking off.
   - `read` → read-only tool subset, thinking off.
   - `act` → full tool set, thinking per user setting.

   This is the single biggest TTFT win available *today*, because most coworking
   turns are `chat` and currently pay for the full tool schemas and a coding
   persona.
2. **Tool-choice discipline.** Ask the model to batch independent tool calls in
   one turn (fewer round-trips = fewer full prefills). Prefer this over
   speculative execution, which is riskier.
3. **Thinking budget as a routing decision**, not a preference. Thinking off by
   default; escalate only in `act`. Document the irreversibility constraint
   where users will see it.
4. **Early exit.** When a `read`-mode answer is complete, end the turn without
   offering another tool round. Some local models always emit a tool call if one
   is available; the router removes the option.

**Acceptance:** in a benchmark of question-shaped prompts, all turns route to
`chat` and TTFT drops measurably with no quality regression on a small eval set.

This router is also what the harness-side "wait is long and schema-dominated"
advice needs to be able to act on: today that case names the cost honestly, but
the schema-light retry it would offer is not wired until C2.1 exists.

### C3 — Streaming and UI responsiveness *(S–M)*

1. **Frame-rate rendering.** Coalesce text deltas into ~16–33ms batches and
   render once per frame (`chat.py` / `renderer.render_agent_run`). Keep the
   accumulating string as the source of truth so final content is exact.
2. **Stop re-parsing Markdown per token.** Render plain text while streaming,
   parse once on `end_assistant_message`.
3. **Cancellation parity.** Make cancellation take effect at tool boundaries
   and in the decode loop, not only between turns.
4. **Off-thread session writes** (or a buffered writer) so persistence never
   blocks the loop.
5. **Status honesty.** Showing TTFT and prompt tokens (shipped) makes
   regressions visible the moment they land.

**Acceptance:** a long streamed reply renders without visible stutter; typing
during generation never blocks.

### C4 — Coworking capabilities (the actual product surface) *(L, staged)*

These are what make it *coworking* rather than "a coding agent pointed at
documents". None of them are latency wins; they are why the latency work
matters.

1. **Multimodal input.** Today `Message.content` is a `str` and `to_api_dict`
   emits text only. Make content a list of parts (`text` | `image`) or add an
   `attachments` field, and wire it through the streaming payload. `mlx_vlm` is
   vision-capable — screenshots, whiteboards and charts are the highest-value
   coworking input and are currently impossible.
2. **Document ingest.** PDF/Markdown/CSV → extract → context, with token
   accounting so a 200-page PDF does not silently blow the prefill budget.
3. **Artifacts as first-class output.** A workspace `outputs/` concept with a
   rendered panel: the agent's deliverable is a file, not a wall of chat.
4. **Personas/modes.** Make the base prompt a *setting* (`mode = coworking |
   coding | research`), not a constant, and let the router select a default.
   `BASE_PROMPT_NO_TOOLS` already exists and is the right shape for `chat`.
5. **Read-only-by-default posture.** Coworking should mostly not write. The
   shipped `--read-only` mode is the mechanism; make it the default for
   `chat`/`read` under the router.
6. **Sessions for conversations.** Titles + search + export (below) matter far
   more in coworking than in coding, because there are many more, shorter
   sessions.
7. **Workspace framing.** Drop the assumption that the CWD is a git repo; a
   workspace is a folder of arbitrary documents.

**Acceptance:** a user can paste a screenshot, attach a PDF, ask a question, and
get an answer whose first token lands inside the budget — with the answer saved
as an artifact.

### C5 — Server warmth and model choice *(M)*

1. **Resident model.** Add `unload_on_idle` (inverse of today's
   unload-on-exit). A cold model start is 20s+ of TTFT; for coworking the model
   should stay warm.
2. **Warm-up prefill** after model start and idle-timeout resets (see C1.2).
3. **Model choice by latency tier.** *(Shipped in 0.111.0: the model menu shows
   the calibrated TTFT / tok/s per model.)*
4. **Draft-model / speculative decoding** and any server-side prefix-cache
   tuning live in `marv-mlx`, not marv — but marv should *measure* them via
   `marv bench` so the runtime work is guided by the same numbers.

### Sequencing

| Milestone | Contents | Outcome |
| --- | --- | --- |
| M1 | measurement — **done** | TTFT is visible in the TUI and in `marv bench`. Every later claim is falsifiable. |
| M2 | C1.1, C1.3, C2.1, C2.3, C3.1 | "Chat feels instant": chat turns ship no schemas, prefix is cacheable, rendering is frame-batched. |
| M3 | C1.2, C1.4, C5.1, C5.2 | TTFT stays flat as a conversation grows. |
| M4 | C4.1, C4.3 | Multimodal in, artifacts out — the coworking surface exists. |
| M5 | C4.2, C4.4, C4.5, C4.6, C4.7 | Positions marv as a coworking harness, not a coding agent. |

M2 is the milestone that decides whether the premise works. If a chat turn
cannot be made to feel instant on the target hardware, the rest is decoration,
and the honest answer is that marv-mlx (server-side prefix caching, speculative
decoding) is the critical path — in which case M2's router work still pays off
but the roadmap shifts toward the runtime.

### Explicitly not proposed

- **A rewrite.** The `llm → runtime → delivery` layering already supports this:
  the router is a runtime concern, attachment plumbing is a `Message` +
  provider-payload concern, rendering is delivery. No layer violations needed.
- **Cloud fallback.** Contradicts local-first; also the one thing that would
  trivially "fix" latency while destroying the product.
- **Speculative/parallel tool execution before the router.** More eager work
  makes TTFT worse, not better, for the common chat turn.
- **Multi-model hot-swapping.** RAM-bound on Apple Silicon; the win is a warm
  single model, not two cold ones.

### Risks

- **The server may not cache prefixes at all.** Then C1.2/C1.4 shrink the prompt
  but cannot stop per-turn re-prefill, and the bottleneck moves to `marv-mlx`.
  Measurement shows this immediately; plan accordingly rather than assuming.
- **Router misclassification.** A `chat`-routed turn that needed a tool becomes
  a bad answer. Mitigate by making the router permissive (bias to `read`) and by
  letting the model escalate within a turn when it emits file/URL references.
- **Lossy history (C1.4) can drop needed detail.** Keep the session as the
  source of truth and render trimmed views from it, so `/context` still shows
  the real thing.
- **Multimodal input changes wire format** and needs provider-side support;
  `apple-fm` is chat-only and will not gain it.

---

## P2 — Session and context UX

### Session titles + searchable session browser
Sessions are flat JSONL files with no title, so `/load` and `marv sessions` are a
list of timestamps. Auto-title each session from its first user message (or a
cheap summary) and show it in the picker; add search/filter once there are dozens.
- Where: `src/marv/runtime/session.py` (`SessionMetadata`), `src/marv/tui/session_panels.py`,
  `src/marv/cli/sessions.py`
- Effort: M · Risk: low

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
RAM/speed status readout. Overlaps C5.1.
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
do not re-render per token. Overlaps C3.1–C3.2.
- Where: `src/marv/tui/chat.py` (`MessageWidget`), `src/marv/tui/renderer.py`
- Effort: M · Risk: medium (Markdown correctness at block boundaries)

### Tool-call diffs
Editing tools show their raw result text. Render `edit`/`write` results as a
syntax-highlighted diff with add/remove bands (and horizontal scroll for long
lines), like other modern terminal agents.
- Where: `src/marv/tui/chat.py` (a new diff view), `src/marv/tools/edit.py`
- Effort: L · Risk: medium

### Turn cost in the footer
The waiting indicator now shows elapsed time (shipped). Still missing: a turn
cost estimate for providers that report pricing.
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

- **Predictable waits (0.111.0).** Per-model prefill/cold-start calibration
  persisted in `state.toml`, a pre-request `WaitEstimate` with tool-schema
  tokens threaded through, the narrated waiting indicator (`prefilling N tokens
  · ~eta · elapsed`), the exit offer for a long-predicted wait, `/compact` on
  demand, and the calibrated latency tier in the model menu. The schema-light
  retry half of the exit offer waits on the C2.1 router.
- **`marv doctor` (0.111.0).** Self-diagnosing setup: config, HF hub, model
  presence, server reachability, download tooling, output budget, extensions.
- **`--read-only` mode (0.111.0).** Narrows the active tool set to
  `read`/`grep`/`find`/`ls`; mutating tools are absent from the schemas and
  refused by the registry.
- **Turn-latency measurement (0.110.0).** TTFT in the status bar, `marv bench`
  with p50/p90 and per-turn growth, and prefill reads no longer killed by a
  read timeout.
- **Menus replace modal windows (0.110.0)** — inline panels at the input line.
- Opt-in tool approval (`approval_mode`, `AGENT_APPROVAL`, `--approval`) with a
  TUI confirm panel and headless deny.
- `marv --version`; `VERSION` as the single version source.
- Release workflow (VERSION/tag check, build, smoke-test, attach assets) and a
  build+smoke-test CI job.
- `CONTRIBUTING.md`, `SECURITY.md`, issue/PR templates, Dependabot.
- `pyproject.toml` metadata (license, authors, keywords, classifiers, URLs).
- Last-used model/thinking persistence + model server auto-start on launch.
- Visible, opt-in model downloads: a missing remembered model opens the download
  picker instead of downloading silently; the picker shows live progress and
  offers curated per-RAM-tier suggestions.
- Status bar shows model RAM usage and generation speed.
- Terminal-native theming (`auto` follows the terminal background) with a quiet,
  rule-based TUI layout, plus screen snapshots guarding it against regressions.
