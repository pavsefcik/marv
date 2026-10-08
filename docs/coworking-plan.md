# Coworking plan — from coding agent to local-LLM coworking harness

Status: **proposal** (not committed scope). `ROADMAP.md` remains the menu.

> **Audit (2026-10-08).** P0 is genuinely shipped (code + tests); P1–P5 are
> genuinely absent. Known P0 caveats are recorded inline below. Updated
> against the working tree that includes the uncommitted latency/panel work.

## 1. The shift

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
   models have coarse abort granularity (see §3.2); you must budget the turn up
   front.
3. **Input and output become multimodal.** Text-only messages and a
   text-only `to_api_dict` cannot support coworking.

Everything below is ordered so that **measurement precedes optimisation** and
**cheap wins precede new capability**.

---

## 2. Latency budget: where the time actually goes

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
- **Tool schemas are always on the wire.** Every turn ships all 7 tool schemas
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

---

## 3. Plan

### P0 — Make TTFT a first-class, measured number — **SHIPPED**

You cannot improve this without data, and headless timing hides the interesting
split. Instrument the client side of the stream:

- In `openai_compat._stream_impl`, record monotonic timestamps at request send
  and first `content`/`reasoning` delta. Emit the result on the existing
  `assistant_metadata` event (already a provider→runtime channel; no new
  protocol).
- Add `prompt_tokens` (a transport-side `len(payload)/4` estimate) and
  `schema_tokens` (the `tools` JSON size) to the same metadata, so prefill cost
  is attributable to prompt vs tool schemas.
- Add a `marv bench` CLI command (and `--json`) that runs N prompts against the
  active model and prints p50/p90 TTFT + prompt-token growth per turn.
- Show **TTFT** and **prompt tokens** in `StatusBar` alongside the existing
  `tok/s` (`src/marv/tui/speed.py`, `src/marv/tui/status.py`).

**Delivered as:** `src/marv/llm/latency.py` (pure tracker + metrics),
`AssistantMetadataEvent` emission in `openai_compat`, `Agent.latency`,
`marv bench` (`src/marv/cli/bench.py`), and `StatusBar.set_ttft`. See
[`llm.md`](llm.md#turn-latency-ttft) and [`cli.md`](cli.md#latency-benchmark).

**Acceptance:** `marv bench` reports TTFT p50/p90 and prefill separately, and a
long-conversation run demonstrably shows TTFT rising with context.

**Audit notes (2026-10-08, verified against the working tree).** Shipped as
described with these caveats:
- Live metadata keys are `prompt_tokens` / `schema_tokens` (not
  `prefill_tokens` / `schemas_tokens`); `LatencySample.prefill_tokens` is the
  derived sum.
- The transport's `prompt_tokens` is a character-based estimate; only
  `Agent.note_request` uses the exact `count_messages_tokens`.
- `Agent` currently threads only `prompt_tokens` into the tracker, so the TUI
  and bench prefill figures are **prompt-only** — `schema_tokens` is dropped by
  `_apply_provider_ttft` / `note_request`.
- `marv bench` does **not** report decode tok/s; it reports TTFT, prefill, and
  growth only. The earlier "TTFT + tok/s" wording was aspirational.
- `refresh_system_prompt()` re-stamps the system prompt mid-session (tool-set and
  extension changes), which strengthens P1.1's importance.

### P1 — Kill prefill: cacheable prefix, smaller prompt *(M–L)*

1. **Prefix stability.** Move the volatile `Date/Time` out of the cached prefix:
   either drop it, reduce it to a date (no time), or place it *after* all static
   content. Add a regression test asserting the system prompt is byte-identical
   across two builds within the same day.
2. **Prefix warm-up.** After a session load or model start, send one cheap
   request containing only the static prefix (persona + `AGENTS.md` + skills
   list) so the server caches it before the user finishes typing. Gate this on
   P0 evidence that the server actually caches; if it does not, skip it and
   pursue 1.4 instead.
3. **Tool schema diet.** Send compact schemas, and gate the tool *set* per turn
   (see P2.1) so a chat turn ships no schemas at all. Remove the duplicate
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

### P2 — Plan the turn before it starts *(M–L)*

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
   turns are `chat` and currently pay for seven tool schemas and a coding
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

### P3 — Streaming and UI responsiveness *(S–M)*

1. **Frame-rate rendering.** Coalesce text deltas into ~16–33ms batches and
   render once per frame (`chat.py` / `renderer.render_agent_run`). Keep the
   accumulating string as the source of truth so final content is exact.
2. **Stop re-parsing Markdown per token.** Render plain text while streaming,
   parse once on `end_assistant_message`.
3. **Cancellation parity.** Make cancellation take effect at tool boundaries
   and in the decode loop, not only between turns.
4. **Off-thread session writes** (or a buffered writer) so persistence never
   blocks the loop.
5. **Status honesty.** Showing TTFT and prompt tokens (P0) makes regressions
   visible the moment they land.

**Acceptance:** a long streamed reply renders without visible stutter; typing
during generation never blocks.

### P4 — Coworking capabilities (the actual product surface) *(L, staged)*

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
5. **Read-only-by-default posture.** Coworking should mostly not write. Promote
   the ROADMAP's `--read-only` work to a default mode for `chat`/`read`.
6. **Sessions for conversations.** Titles + search + export (already on the
   roadmap) matter far more in coworking than in coding, because there are many
   more, shorter sessions.
7. **Workspace framing.** Drop the assumption that the CWD is a git repo; a
   workspace is a folder of arbitrary documents.

**Acceptance:** a user can paste a screenshot, attach a PDF, ask a question, and
get an answer whose first token lands inside the budget — with the answer saved
as an artifact.

### P5 — Server warmth and model choice *(M, gated on P0)*

1. **Resident model.** Add `unload_on_idle` (inverse of today's
   unload-on-exit). A cold model start is 20s+ of TTFT; for coworking the model
   should stay warm.
2. **Warm-up prefill** after model start and idle-timeout resets (see P1.2).
3. **Model choice by latency tier.** Curated suggestions should expose TTFT and
   tok/s, not just RAM fit, so users can trade quality for responsiveness
   knowingly.
4. **Draft-model / speculative decoding** and any server-side prefix-cache
   tuning live in `marv-mlx`, not marv — but marv should *measure* them via
   `marv bench` so the runtime work is guided by the same numbers.

---

## 4. Sequencing

| Milestone | Contents | Outcome |
| --- | --- | --- |
| M1 | P0 — **done** | TTFT is visible in the TUI and in `marv bench`. Every later claim is falsifiable. |
| M2 | P1.1, P1.3, P2.1, P2.3, P3.1 | "Chat feels instant": chat turns ship no schemas, prefix is cacheable, rendering is frame-batched. |
| M3 | P1.2, P1.4, P5.1, P5.2 | TTFT stays flat as a conversation grows. |
| M4 | P4.1, P4.3 | Multimodal in, artifacts out — the coworking surface exists. |
| M5 | P4.2, P4.4, P4.5, P4.6, P4.7 | Positions marv as a coworking harness, not a coding agent. |

M2 is the milestone that decides whether the premise works. If a chat turn
cannot be made to feel instant on the target hardware, the rest is decoration,
and the honest answer is that marv-mlx (server-side prefix caching, speculative
decoding) is the critical path — in which case M2's router work still pays off
but the roadmap shifts toward the runtime.

---

## 5. Explicitly not proposed

- **A rewrite.** The `llm → runtime → delivery` layering already supports this:
  the router is a runtime concern, attachment plumbing is a `Message` +
  provider-payload concern, rendering is delivery. No layer violations needed.
- **Cloud fallback.** Contradicts local-first; also the one thing that would
  trivially "fix" latency while destroying the product.
- **Speculative/parallel tool execution before the router.** More eager work
  makes TTFT worse, not better, for the common chat turn.
- **Multi-model hot-swapping.** RAM-bound on Apple Silicon; the win is a warm
  single model, not two cold ones.

---

## 6. Risks

- **The server may not cache prefixes at all.** Then P1.2/P1.4 shrink the prompt
  but cannot stop per-turn re-prefill, and the bottleneck moves to `marv-mlx`.
  P0 will show this immediately; plan accordingly rather than assuming.
- **Router misclassification.** A `chat`-routed turn that needed a tool becomes
  a bad answer. Mitigate by making the router permissive (bias to `read`) and by
  letting the model escalate within a turn when it emits file/URL references.
- **Lossy history (P1.4) can drop needed detail.** Keep the session as the
  source of truth and render trimmed views from it, so `/context` still shows
  the real thing.
- **Multimodal input changes wire format** and needs provider-side support;
  `apple-fm` is chat-only and will not gain it.
