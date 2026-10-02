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

`model` can be omitted for the `ymlx` provider: when unset, marv auto-selects the
currently-serving model on the ymlx port (or you can pick from the TUI model list,
which enumerates every model ymlx manages).

## YMLX (default)

- Default `provider` is `ymlx` and default `base_url` is `http://localhost:11500`.
- No API key is required — YMLX serves a local, unauthenticated endpoint.
- Selecting a model starts/swaps it via the `ymlx` CLI (`ymlx run <id>`).
- Model discovery reads the local HF hub (`~/.cache/huggingface/hub`).

Optional `[providers.ymlx]` overrides: `base_url`, `hub_dir`, `primary_port`, `ymlx_command`.

## OpenAI-compatible (generic)

Any OpenAI-compatible server can be used by setting `provider` to a custom name with a
provider override (e.g. Ollama, LM Studio). Set `model` explicitly unless the provider
auto-resolves a running one.

## Context files

`AGENTS.md` and `CLAUDE.md` are auto‑loaded from the project and its ancestors to seed the system prompt.

## Extension config snapshot

Extensions do not receive the raw `Config` object, but `ctx.config` exposes a safe snapshot of the resolved runtime configuration. That includes the resolved provider/model selection, prompt/context directories, and provider bootstrap values needed for extension-managed child agents.
