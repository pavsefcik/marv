# LLM Providers & Streaming

Providers implement a shared interface (`LLMProvider`) that exposes:

- `stream(messages, tools, options)` — event‑based streaming
- `count_tokens`, `count_messages_tokens` — for context budgeting
- `supports_thinking()` — capability detection
- `list_models()` — model discovery

## Provider bootstrap

- `src/marv/llm/factory.py` is the bootstrap boundary:
  - `resolve_provider_config(...)` resolves base URL/model/api key from flat inputs + optional provider overrides.
  - `create_provider(...)` validates provider/model compatibility and returns a concrete provider instance.
- Delivery shells construct providers through the factory and inject them into the runtime `Agent`.

## Built‑in providers

Providers that own a local server process share `LocalServerProvider`
(`src/marv/llm/local_server.py`), which handles start-on-demand, model switching,
readiness polling, process naming, and unload-on-exit. Subclasses only describe
how to launch their server and how to tell it is ready.

### marv-mlx (default)

- Implementation: `src/marv/llm/marv_mlx.py`, family classification in `src/marv/llm/mlx_models.py`,
  downloads in `src/marv/llm/model_download.py`.
- **Server lifecycle** — by default marv launches `mlx_vlm.server` itself
  (`mlx_vlm.server --host 127.0.0.1 --model <id> --port 11500`), located via
  `resolve_mlx_server_command()`. **Seam A:** marv is self-sufficient — `marv-mlx`
  is the recommended runtime and model manager but not a runtime dependency, and
  no external model-manager CLI is required to run the agent. `marv mlx …` is a
  thin passthrough to the `marv-mlx` CLI when it is installed.
- **Seam B (opt-in)** — with `server_manager = "marv-mlx"` (or
  `AGENT_SERVER_MANAGER=marv-mlx`, or `marv run --server-manager marv-mlx`) marv
  delegates model lifecycle to the runtime: `list_models()` →
  `marv-mlx list --json`, `ensure_running()` → `marv-mlx run <model>`,
  `stop()` → `marv-mlx stop`. The client (`src/marv/llm/marv_mlx_cli.py`)
  degrades to the embedded path on any CLI failure or missing binary, so
  `embedded` stays the safe default.
- **Model discovery** — `list_models()` scans the local HF hub
  (`~/.cache/huggingface/hub/models--*`) and collapses Ministral Instruct/Reasoning
  pairs into a single entry. Models can also be downloaded with
  [marv-mlx](https://github.com/pavsefcik/marv-mlx) or `hf`; marv reads the same
  hub either way.
- **Model downloads** — `src/marv/llm/model_download.py` fetches a missing model
  into the hub (running `huggingface_hub.snapshot_download` in the `mlx-vlm`
  interpreter, since marv itself has no HF dependency) and streams progress
  events back; `src/marv/tui/download_panel.py` renders them. The TUI asks before
  downloading a missing remembered model, and `/model <id>` downloads before
  switching. Curated per-RAM-tier suggestions ship in `model_download.py`.
- **Thinking** — `enable_thinking` is sent for template families (Qwen/Gemma), `[THINK]`
  bracket markers for Ministral Reasoning. The reasoning trace arrives in
  `reasoning_content` and is surfaced as thinking events. `supports_thinking()` is
  family-accurate.
- **Process naming** — the server is launched with `MARV_PROCTITLE` and a bundled
  `_proctitle/sitecustomize.py` hook, so Activity Monitor / `ps` show the model id
  instead of "Python".

### apple-fm

- Implementation: `src/marv/llm/apple_fm.py`.
- Launches `fm serve --port 1976` (macOS 27+) and talks to its OpenAI-compatible
  endpoint. One server serves every model, so switching between `system` and `pcc`
  does not restart anything.
- **Chat-only** — `fm serve` has no OpenAI function/tool calling, so the provider
  declares `supports_tools = False`; the agent hides its tool suite and answers in
  plain text. `supports_thinking()` is `False`.

### Teardown (all local servers)

- `LocalServerProvider.close()` unloads the model. `atexit` plus `SIGTERM`/`SIGHUP`
  handlers (`llm/server_lifecycle.py`) cover crashes and abrupt exits; `SIGKILL`
  cannot be intercepted. A server marv attached to (rather than launched) is found
  via `lsof` on the port and stopped too.

### Timeouts

- The generic OpenAI-compatible transport uses a 120 s read timeout: cloud
  endpoints keep an SSE connection warm, so silence is a real failure.
- Local-server backends set no read timeout (`DEFAULT_LOCAL_READ_TIMEOUT`). A
  large prompt takes minutes of silent prefill in `mlx_vlm` before the first
  token, and killing it there is wrong. `AGENT_LLM_READ_TIMEOUT` overrides the
  read timeout for any provider (`off`/`0` disables; integer seconds otherwise).
- A `httpx.ReadTimeout` **before the first token** is wrapped in
  `PrefillTimeoutError`, which is explicitly non-retriable. A timeout *after*
  tokens arrive is a real transport drop and is retried as before. The connect
  timeout (10 s) is unaffected.

### OpenAI-compatible

- Implementation: `src/marv/llm/openai_compat.py`.
- A generic transport for any OpenAI-compatible endpoint (Ollama, LM Studio, …).
- Requires an explicit `model` unless the configured provider auto-resolves a running one.

## Model validation policy

- Provider/model compatibility is enforced in the LLM module:
  - factory-time validation in `create_provider(...)`
  - runtime validation in provider `set_model(...)`
- Invalid combinations fail with a `ValueError`.

## Streaming events

The stream yields structured events:

- `text_start`, `text_delta`, `text_end`
- `thinking_start`, `thinking_delta`, `thinking_end`
- `toolcall_start`, `toolcall_delta`, `toolcall_end`
- `done` / `error`

These events are consumed by the agent loop and TUI to render incremental updates.
The OpenAI-compatible transport parses `reasoning_content`/`reasoning` deltas into the
thinking events, so MLX reasoning shows up in the TUI automatically.

## Turn latency (TTFT)

Time-to-first-token is measured, not estimated, because for a local model it is
the difference between a responsive partner and an interruption. The
OpenAI-compatible transport times each turn from just before the request is
handed to the transport to the first decoded token (text or thinking), and
publishes the result as `assistant_metadata`:

```json
{"latency": {"ttft_ms": 812.4, "prompt_tokens": 3100, "schema_tokens": 640,
             "streamed": true, "token_events": 214}}
```

Design notes:

- **Emitted on the existing channel.** `assistant_metadata` was already a
  provider→runtime path, so latency needed no new stream event. Nested payloads
  are merged, so latency and provider-specific artifacts can coexist.
- **Prompt cost travels with the timing.** TTFT alone cannot distinguish "the
  model is slow" from "the prompt grew"; `prompt_tokens` and `schema_tokens`
  make the prefill cost attributable, and are kept separate because tool
  schemas are avoidable cost on a turn that needs no tools. `Agent` threads
  both into the tracker (the transport's `schema_tokens` refines its own
  pre-request estimate), so the TUI and `marv bench` prefill figures include
  the schemas that were actually on the wire.
- **A turn that never streams reports `streamed: false`.** It is excluded from
  medians rather than recorded as an infinite TTFT.
- **The runtime may prefer its own clock.** `Agent` times the same interval from
  `note_request` to the first delta; a provider-reported TTFT refines that last
  sample, since the transport excludes the loop's bookkeeping. This keeps
  providers that do not report timing (the test fake, `apple-fm`) measurable.

Consumers: `marv bench` (aggregate report + seeding the calibration), the TUI
status bar (last turn), and the wait predictor that needs to know the cost of a
turn before sending it.

## Prefill/decode calibration

The measured samples' other job is to teach the harness how fast *this machine*
prefills, so a wait can be predicted before it starts rather than discovered by
sitting through it. `LatencyFit` (`src/marv/llm/latency.py`) is the learned
constant, and it is deliberately small:

```
TTFT ≈ prefill_tokens / prefill_tokens_per_second + cold_start_ms
```

- **Managed by a through-origin aggregate.** `prefill_tokens_per_second` is
  `total_prefill_tokens / total_prefill_seconds` over warm samples, not a
two-point slope, so one noisy turn cannot produce an absurd estimate. A model
with no usable samples reports `has_data == false`, and
`predict_ttft_ms()` returns `None` — the UI then degrades to elapsed-only
instead of showing a fabricated ETA.
- **Cold start is a separate regime.** The first turn after the model is loaded
pays weight-loading time that is independent of prompt size. It is learned as a
`cold_start_ms` constant (excluded from the prefill rate, and only once a warm
rate exists to subtract the prefill cost from), so a cold turn does not drag
the rate down.
- **A cache hit is not a faster prefill.** When the server has a warm prefix
cache a large prompt comes back far below the current prediction. A sample
faster than `CACHE_HIT_RATIO` (0.5×) of the fit is counted as a `cache_hit` and
kept out of the rate: it is a real speedup, but folding it in would
under-predict every later uncached turn. A sample below `MIN_PREFILL_MS` is
treated as too cheap to inform the rate.
- **Where the fit comes from.** The runtime learns it from the turns it already
measures (`Agent.refine_latency_fit()`), and `marv bench` seeds it on a fresh
machine (see [`cli.md`](cli.md#latency-benchmark)). Either way it is persisted
per model in `state.toml` beside the last-used selection
(`remember_latency_fit`) and re-loaded on the next start
(`Config.latency_fit_for(model)`).

See [`configuration.md`](configuration.md#last-used-selection) for where the fit
is stored.

## Wait prediction

`WaitEstimate` carries the pre-request prediction to delivery. It is computed
when the request is built (`Agent._agent_loop`, which already knows the exact
prompt size and the tool-schema cost) and emitted as a `WaitEstimateChunk`
before the provider request goes out, so the UI can narrate the prefill from
its first second. `schema_tokens` is threaded through here rather than dropped,
so tool schemas — on the wire every turn — are part of the estimate.

- `eta_ms is None` is a first-class value: on a machine with no measured turns
there is no honest ETA, and delivery must degrade to elapsed-only.
- `dominant_cost` (`cold` | `prompt` | `schemas`) and `prompt_share` reuse the
  prompt/schema split to attribute the wait.
- `wait_advice()` turns a long prediction into a specific exit offer (compact,
or a schema-light turn), and returns `None` for an ordinary wait or a cold
start where nothing can be shrunk. It is pure and display-only; the prediction
is advisory and never caps the wait.

See [`tui.md`](tui.md#legible-waits) for how the indicator renders it.
