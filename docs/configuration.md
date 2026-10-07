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
  manager is required. [marv-mlx](https://github.com/pavsefcik/marv-mlx) is the
  recommended runtime/manager (and backs the `marv mlx …` passthrough), but the
  harness stays self-sufficient without it. Models can also be downloaded with
  marv-mlx or `hf`.
- `server_manager` selects the local-server lifecycle: `embedded` (default;
  marv launches `mlx_vlm.server` itself) or `marv-mlx` (delegate to the
  `marv-mlx` runtime CLI when present, falling back to embedded). Override with
  `AGENT_SERVER_MANAGER` or `marv run --server-manager marv-mlx`.
- Model discovery reads the local HF hub (`~/.cache/huggingface/hub`). A model that
  is not in the hub is downloaded only after you ask: the TUI opens a download
  picker (curated suggestions for your RAM tier, or any Hugging Face id) and shows
  live progress. Downloads run in the `mlx-vlm` interpreter and land in the same hub.

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

## Last-used selection

The provider, model, and thinking level you used most recently are remembered in
`state.toml`, written beside the session directory (`~/.cache/marv/state.toml` by
default). On the next start marv reapplies them and begins loading that model's
server immediately, so a fresh session does not have to stop at the model picker.

This file is the weakest configuration source: an explicit config file value, an
`AGENT_*` environment variable, or a CLI flag (`-m`/`-p`/`-t`) always overrides it.
Delete the file to forget the selection.

## Tool approval

`approval_mode` controls whether tool calls need explicit confirmation before they
run. It is `off` by default, so marv behaves as before unless you opt in.

- `off` — every tool call runs without asking.
- `destructive` — ask before `write`/`edit` and before risky shell commands
  (file redirection, `rm`/`mv`/`chmod`, `git push`/`reset`/`clean`, piping into a
  shell, and similar). Read-only tools run freely.
- `all` — ask before every tool call.

Set it in config, via `AGENT_APPROVAL`, or with `marv run --approval <mode>`.

In the TUI a `destructive` or `all` decision opens a Yes/No confirmation. In
headless mode there is no one to ask, so a tool that needs approval is denied
with a message rather than run. This is a guardrail, not a sandbox — see
[SECURITY.md](../SECURITY.md).

## Context files

`AGENTS.md` and `CLAUDE.md` are auto‑loaded from the project and its ancestors to seed the system prompt.

## Extension config snapshot

Extensions do not receive the raw `Config` object, but `ctx.config` exposes a safe snapshot of the resolved runtime configuration. That includes the resolved provider/model selection, prompt/context directories, and provider bootstrap values needed for extension-managed child agents.
