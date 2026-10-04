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
- **Server lifecycle** — marv launches `mlx_vlm.server` itself
  (`mlx_vlm.server --host 127.0.0.1 --model <id> --port 11500`), located via
  `resolve_mlx_server_command()`. No external model-manager CLI is involved.
- **Model discovery** — `list_models()` scans the local HF hub
  (`~/.cache/huggingface/hub/models--*`) and collapses Ministral Instruct/Reasoning
  pairs into a single entry. Models can also be downloaded with YMLX or `hf`; marv
  reads the same hub either way.
- **Model downloads** — `src/marv/llm/model_download.py` fetches a missing model
  into the hub (running `huggingface_hub.snapshot_download` in the `mlx-vlm`
  interpreter, since marv itself has no HF dependency) and streams progress
  events back; `src/marv/tui/download_modal.py` renders them. The TUI asks before
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