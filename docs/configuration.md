# Configuration

Configuration is layered and merged in this order (lowest → highest):

1) Defaults
2) Global config (`~/.marv/config.toml` or `.yaml`)
3) Project config (`.marv/config.toml` or `.yaml`)
4) Environment variables (`AGENT_*`)

## Runtime split

- `Config` (`src/marv/config/runtime.py`) is a delivery/bootstrap concern.
- The runtime uses `AgentSettings` (`src/marv/runtime/settings.py`), projected from config via `Config.to_agent_settings()`.
- Provider construction and provider/model resolution are handled in `src/marv/llm/factory.py`, not in the runtime layer.

## Common settings

- `provider`, `model`, `api_key`, `base_url`
- `context_max_tokens`, `max_output_tokens`, `temperature`
- `skills_dirs`, `prompt_template_dirs`, `extensions`
- `custom_system_prompt`, `append_system_prompt`
- `providers.<name>.base_url`, `providers.<name>.model`, `providers.<name>.api_key`

`model` can be omitted for the `marv-mlx` provider: when unset, marv auto-selects the
currently-loaded MLX model (or you can pick from the TUI model list, which enumerates
every model downloaded in the local HF hub).

Leave `base_url` unset to use the backend's default; a top-level `base_url` overrides
the per-backend default for whichever provider is active.

## marv-mlx (default)

- Default `base_url` is `http://localhost:11500`.
- No API key is required — the endpoint is local and unauthenticated.
- marv launches `mlx_vlm.server` itself and stops it on exit; no external model
  manager is required. Models can be downloaded with YMLX or `hf`.
- Model discovery reads the local HF hub (`~/.cache/huggingface/hub`).

## apple-fm (macOS 27+)

- Set `provider = "apple-fm"`; default `base_url` is `http://127.0.0.1:1976`.
- `model` is `system` (on-device) or `pcc` (Private Cloud Compute); default `system`.
- Run `sudo fm license` once to accept the Foundation Models CLI terms.
- Chat-only: `fm serve` has no function/tool calling, so marv's tool suite is disabled
  while this provider is active.

## OpenAI-compatible (generic)

Any OpenAI-compatible server can be used by setting `provider` to a custom name with a
provider override (e.g. Ollama, LM Studio). Set `model` explicitly unless the provider
auto-resolves a running one.

## Context files

`AGENTS.md` and `CLAUDE.md` are auto‑loaded from the project and its ancestors to seed the system prompt.

## Extension config snapshot

Extensions do not receive the raw `Config` object, but `ctx.config` exposes a safe snapshot of the resolved runtime configuration. That includes the resolved provider/model selection, prompt/context directories, and provider bootstrap values needed for extension-managed child agents.
