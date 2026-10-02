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

### YMLX (default)

- Implementation: `src/marv/llm/ymlx.py`, family classification in `src/marv/llm/ymlx_models.py`.
- Talks to the local YMLX server on `http://localhost:11500` (no API key).
- **Server lifecycle** — `YMLXProvider.ensure_running()` shells out to the `ymlx` CLI
  (`ymlx run <id>` / `ymlx stop`) so selecting a model starts/swaps it on the ymlx port.
- **Model discovery** — `list_models()` scans the local HF hub
  (`~/.cache/huggingface/hub/models--*`) and collapses Ministral Instruct/Reasoning
  pairs into a single entry.
- **Thinking** — `enable_thinking` is sent for template families (Qwen/Gemma), `[THINK]`
  bracket markers for Ministral Reasoning. The reasoning trace arrives in
  `reasoning_content` and is surfaced as thinking events. `supports_thinking()` is
  family-accurate.

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
thinking events, so YMLX reasoning shows up in the TUI automatically.