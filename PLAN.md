# MARV — YMLX-powered Coding Agent TUI: Build Plan

Status: proposal for review. Heavy lift verified against the real base repo
(`~/.cache/huggingface/hub` has real models installed) and the real `mlx_vlm`
server source on this machine.

---

## 0. What I verified (so the plan rests on facts, not assumptions)

1. **YMLX is already OpenAI-compatible.** `mlx_vlm.server` exposes `/v1/chat/completions`,
   `/v1/completions`, `/v1/models`, etc. on `localhost:11500` (or next free port).
   - The base repo's `OpenAICompatibleProvider` (`src/agent/llm/openai_compat.py`)
     already talks to exactly this endpoint (httpx, SSE streaming, dynamic tool calls,
     `tools`/`tool_choice` payload, `/v1/models` list). This collapses a large chunk of
     the "YMLX provider" work — it is mostly a *subclass/wrapper*, not a new transport.
2. **Tool calling works.** `mlx_vlm/server/openai.py` honors `tools` + `tool_choice`
   (none/auto/required/specific) and `process_tool_calls(...)`; `tools/policy.py`
   implements OpenAI `tool_choice` semantics. The base `openai_compat` provider already
   sends/parses `delta.tool_calls`, so read/write/edit/bash + tool loop works out of the box.
3. **Thinking is supported but not wired in the base.** The server emits
   `reasoning_content`/`reasoning` in the delta and honors `enable_thinking`,
   `thinking_start_token`/`thinking_end_token` (Ministral bracket family, `[THINK]`).
   The base `openai_compat.py` **does not** send `enable_thinking` and **does not** parse
   `reasoning_content` into thinking events — this is the one real compatibility gap.
4. **Model list comes from the HF hub dir, not `/v1/models`.** ymlx manages models under
   `~/.cache/huggingface/hub/models--org--name` (this machine has Ministral-3-8B pair,
   Qwen3.5/3.6 4B/9B/35B, prism-ml Bonsai). `/v1/models` only reports the server's
   *running* model. So the ymlx provider needs a custom `list_models()` that scans the hub.
5. **Config divide.** Base repo uses TOML (`~/.agent/config.toml`). ymlx uses zsh-source
   config (`~/.cache/ymlx/config.zsh`). The app should **not** try to parse zsh.
6. **License flag.** Base README says MIT but the checkout has **no `LICENSE` file**.
   Must confirm on GitHub before redistributing (keep attribution if required).
7. **Lib count:** base `src/` is ~16.3k LOC across 83 files — small enough to strip
   cleanly; `make test`/`make lint` are the regression gate.

---

## 1. Open questions needing your call (before/at the points noted)

| # | Question | Blocking | Default if deferred |
|---|----------|----------|---------------------|
| Q1 | **App name** (directory is `marv` — suggest below) | Blocks Phase 5 rebrand | `marv` |
| Q2 | License: OK to strip `openai`/`anthropic`/`openai-codex` providers + web + extensions? I checked upstream has no LICENSE file | Phase 1 strip | Keep a placeholder, verify on GitHub first |
| Q3 | Server lifecycle: agent shell out to `ymlx` CLI vs. spawn `mlx_vlm.server` directly | Phase 2 | Shell out to `ymlx` (reuses warmup/port/titling/detach) |
| Q4 | Where app config lives & format (`~/.cache/marv/config.toml` vs reuse ymlx dir) | Phase 4 | `~/.cache/marv/config.toml`, references ymlx hub for models |
| Q5 | Keep `openrouter`/`groq`/`ollama` compat providers as a fallback or ymlx-only | Phase 1 strip | ymlx-only (drop others) |
| Q6 | Python 3.14 + `uv` (base requirement) on this machine | Phase 0 spike | Move to what's installed; verify |

**Name suggestions** (pick one, or propose your own): **marv** (short, matches dir),
**yamlux/ymlax**, **helios**, **loom**, **ferrite**, **anvil**, **forge**.
Recommendation: **marv** — the directory, binary `marv`, package `marv`, sessions
`~/.cache/marv/`, matches the ymlx "boat/machine part" vibe.

---

## 2. Execution phases (each ends in a green gate: `make test` + `make lint`)

### Phase 0 — Verify feasibility spike (before writing code)
- Start a *small* installed model (e.g. Qwen3.5-4B) via `mlx_vlm.server` on :11500.
- Smoke-test with the base repo: `make run` headless, ask it to `ls` a dir → confirms
  tools + tools loop + streaming actually work against ymlx end to end.
- Confirm Python/uv on this machine; note the model the machine defaults to.
- **Exit:** a working chat-with-tools against ymlx using the *unmodified* base.

### Phase 1 — Fork & strip (+ verify license)
- Confirm license on GitHub; add attribution note if needed.
- Clone base repo into `marv`, point git remote to a new `marv` remote (rename).
- Rename package `agent` → `marv` module namespace; update pyproject name; `make deps`.
- **Strip** (after Q2/Q5):
  - Remove `web/` (FastAPI + WS delivery, static assets).
  - Remove providers `openai/`, `anthropic/`, `openai_codex/` (incl. both `oauth.py`,
    `pricing.py`), and their CLI `auth` commands.
  - Remove `extensions/` host + TUI extension bridge **if** unneeded (Q2/Q5).
  - Remove `openrouter`/`groq`/`ollama` default configs unless kept as fallback (Q5).
  - Keep: `tools/`, `skills/`, `prompts/`, `runtime/` (loop, sessions, compaction,
    approval gates), `tui/`, `config/`, `cli/` (minus auth), `llm/` (minus stripped).
- Prune docs that reference removed pieces; update README opening.
- First TUI bootstrap (`make run`) shows the *stripped* app on the local endpoint.
- **Exit:** `make test` + `make lint` green; app launches, stripped references gone.

### Phase 2 — YMLX provider + model picker + server lifecycle
- New `src/agent/llm/ymlx.py`: `YMLXProvider(OpenAICompatibleProvider)` with:
  - `base_url` default `http://localhost:11500`, `api_key` `""` (none required).
  - `name = "ymlx"`; register in `factory.resolve_provider_config` + `create_provider`
    defaults (`base_url=http://localhost:11500`) and `config/default.toml`
    (`provider = "ymlx"`, `base_url`, no api-key).
  - Override `list_models()` → scan `~/.cache/huggingface/hub/models--*` (normalize
    `--`→`/`), collapse Ministral Instruct/Reasoning into one entry, plus include the
    currently running model from `/v1/models`. (If already running on :11500, keep.)
  - `set_model()`/`ensure_running()` orchestrating server startup (Q3):
    * ymlx CLI path (recommended): `ymlx run --model <id>` (headless, primary port,
      detached, warmup, process title) then poll `/v1/models`.
    * direct path: `mlx_vlm.server --model <id> --port 11500 ...` (read advanced flags).
  - `stop()` / `close()` → `ymlx stop` (or SIGTERM child).
- In the TUI: `ModelModal` already calls `self._agent.list_models()` — wire it so the
  dropdown lists ymlx-managed models; picking + save triggers provider
  switch (ensure server running, swap if a different model is live).
- **Exit:** TUI model picker lists hub models; picking one starts/swaps the server and
  chat streams; `make test`/`make lint` green (with a fake hub-dir + mock server test).

### Phase 3 — Thinking/reasoning mapping (the real compatibility gap)
- Extend `YMLXProvider.stream()` payload: send `enable_thinking` (from active
  `ThinkingLevel`), and `thinking_start_token`/`thinking_end_token` (`[THINK]`) for the
  Ministral bracket family only.
- Parse `delta.reasoning_content`/`delta.reasoning` in the SSE loop → emit the runtime's
  `thinking_start` / `thinking_delta` / `thinking_end` events (mirror how `openai.py`
  / `anthropic.py` emit thinking). Keep independent from `content` (answer) stream.
- Override `supports_thinking()` using ymlx model-family classification (qwen/gemma =
  template bool; ministral-reasoning/bracket; lfm/none = display-only), so
  `ModelModal` thinking radio reflects the real model, not name heuristics.
- **Exit:** streaming chat shows thinking trace correctly (and honours off/on), tools
  still work; tests green.

### Phase 4 — Config repurpose
- App config `~/.cache/marv/config.toml` (or per Q4), `version: 1`:
  - `[marv]` agent settings: `default_model`, `approval_mode` (map to runtime's
    existing approval gates), `system_prompt`, `context_max_tokens`, `max_output_tokens`,
    `temperature`, `thinking_level`.
  - `[ymlx]` integration: `server_command` (ymlx | direct), `base_url`, `hub_dir`
    (default `~/.cache/huggingface/hub`), `primary_port` (11500), `launch_flags`
    forwarded from ymlx's config.
- Wire into `config/runtime.py` `Config` dataclass + `Config.load()` (global→project→env
  layering already there). Ready defaults seed the model picker; referencing ymlx's
  `config.zsh` for defaults only when a corresponding field is unset (don't parse zsh).
- **Exit:** `marv` honours a versioned local config; model list/default pulled from ymlx
  state; `make test`/`make lint` green.

### Phase 5 — Rebrand (needs Q1 answer)
- Rename all user-facing strings, TUI title/branding, binary entry point, session/config
  paths (`~/.cache/marv/`), package metadata, docs.
- Add `marv-launcher.zsh`-style shell entry if desired, matching ymlx UX.
- **Exit:** `grep -ri "my-own-coding-agent" src/` (and rendered UI) returns nothing.

### Phase 6 — Polish + acceptance
- TUI status line / output formatting tweaks (gather specifics from you).
- Acceptance runbook:
  - `make run` → chat with a ymlx model, streaming + tools (read/write/edit/bash).
  - Model picker lists ymlx hub models from marv config; switching works.
  - Sessions save/resume under `~/.cache/marv/sessions`.
  - Approval gates active.
  - `make test` + `make lint` green.

---

## 3. Key risks & mitigations
- **Unsure if the model the agent picks can do tool calling well** (local small models are
  erratic at function calling). Phase 0 spike on Qwen3.5-4B is the gate; mitigations:
  prompt hardening, `tool_choice` steering (request supports it), or a "tools toggle".
- **Server swap latency**: switching models re-loads weights (large). Mitigation: reuse
  ymlx warmup + explicit "swap" UX from the handoff.
- **License ambiguity** → resolve before Phase 1 redistribution.
- **Ministral pair handling** must stay collapsed in UI but resolve to the right half when
  thinking toggles (mirror ymlx's sibling logic in `list_models`).

---

## 4. Suggested first work items (once you answer Q1, Q2, Q5)
1. Answer the Q-table (esp. **name**, **license**, **provider strip set**, **server
   lifecycle**).
2. Run Phase 0 spike (I can do this now — small model, unmodified base → tools chat).
3. Start Phase 1 fork/strip.