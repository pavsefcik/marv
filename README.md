# marv

A local-first coding agent TUI for Apple Silicon. **marv** is a lean, hackable
agent harness that runs entirely on-device — no cloud accounts, no API keys.

marv runs local MLX models through **`marv-mlx`** (the default backend): it
launches [`mlx-vlm`](https://github.com/Blaizzy/mlx-vlm)'s `mlx_vlm.server`
itself and runs any MLX model downloaded in the local Hugging Face hub, with
family-accurate thinking support. Models can be downloaded and managed with
[marv-mlx](https://github.com/pavsefcik/marv-mlx) — the runtime layer of the
MARV family (see below) — or with `hf`; marv reads the same hub and has **no
runtime dependency** on either. `marv mlx …` passes through to the `marv-mlx`
CLI when it is installed.

## The MARV family

**MARV** — *Modular Agent Runtime Valve* — is two layers:

```
user ──► marv   (harness: agent loop · tools · sessions · skills · Textual TUI)
            │  OpenAI-compatible HTTP :11500 + lifecycle CLI
            ▼
         marv-mlx (runtime: catalog · download · run/swap · mlx_vlm.server)
            │
            ▼
         Hugging Face hub + Apple Silicon (MLX)
```

- **[marv](https://github.com/pavsefcik/marv)** (this repo) is the harness. It
  keeps its name, package, binary and state paths.
- **[marv-mlx](https://github.com/pavsefcik/marv-mlx)** is the runtime: it
  manages and serves local models. marv self-manages `mlx_vlm.server`, so the
  runtime is recommended, not required; `marv mlx …` forwards to its CLI.

The two layers share the Hugging Face hub and the `~/.cache/marv/` cache root
(harness state at the top, runtime state under `mlx/`), but own disjoint env
namespaces (`AGENT_*` for the harness, `MARV_MLX_*` for the runtime).

## Why this exists

To own the whole agent harness end-to-end — and to run it on the models you
already have locally. It is not the best agent; it is _readable, hackable, and
yours_.

## Requirements

- Apple Silicon Mac
- Python 3.14+ and `uv`
- For `marv-mlx`: the `mlx-vlm` tool — `uv tool install mlx-vlm --with jinja2 --with setproctitle`
  (the `setproctitle` extra is optional and only affects the process name)

## Install

marv is distributed as GitHub release artifacts (there is no PyPI package yet).
Download the wheel from the [latest release](https://github.com/pavsefcik/marv/releases/latest)
and install it with `uv`:

```sh
uv tool install "./marv-X.Y.Z-py3-none-any.whl"
marv --version
```

Or install straight from the release URL (replace `X.Y.Z` with the version):

```sh
uv tool install "marv @ https://github.com/pavsefcik/marv/releases/download/vX.Y.Z/marv-X.Y.Z-py3-none-any.whl"
```

## Quickstart

From a repository checkout:

```sh
make deps      # uv sync
make run       # start the TUI; pick a model, marv loads it for you
```

Headless (single prompt, model loaded on demand):

```sh
make run-headless PROMPT="List all Python files"
uv run marv run --headless -m mlx-community/Qwen3.5-4B-MLX-4bit "Say hi"
```

marv starts the model server itself and stops it when it exits. The model picker
lists every MLX model downloaded in `~/.cache/huggingface/hub`, and selecting
one starts/swaps the server. If a model is already selected — from config, a
resumed session, or the last-used state — marv starts its server on launch
instead of waiting for the first prompt.

## Shell launcher

To make `marv` available in any new shell, source the launcher from `~/.zshrc`:

```sh
echo 'test -f "'$PWD'/marv-launcher.zsh" && source "'$PWD'/marv-launcher.zsh"' >> ~/.zshrc
source ~/.zshrc
```

The launcher runs the marv CLI from this repo (using `.venv/bin/marv` when
present, else `uv run`).

## Configuration

Config is TOML, layered global → project → env:

- Global: `~/.marv/config.toml`
- Project: `./.marv/config.toml`
- Env: `AGENT_*` variables (`AGENT_MAX_OUTPUT_TOKENS`, `AGENT_TEMPERATURE`, …)

State (sessions, skills, prompt templates) lives under `~/.cache/marv/`. The
last-used provider/model/thinking selection is remembered in
`~/.cache/marv/state.toml` and reapplied on the next start; explicit config files,
`AGENT_*` variables, and CLI flags still win over it.

Set `approval_mode` (or `AGENT_APPROVAL`, or `marv run --approval`) to
`destructive` or `all` to confirm risky tool calls before they run. In headless
mode there is no one to ask, so an unapproved tool is denied. This is a
guardrail, not a sandbox — see [SECURITY.md](SECURITY.md).

The local-server lifecycle is controlled by `server_manager` (`embedded`, the
default, where marv launches `mlx_vlm.server` itself, or `marv-mlx`, which
delegates to the [marv-mlx](https://github.com/pavsefcik/marv-mlx) runtime CLI
when installed and falls back to embedded otherwise). Override it with
`AGENT_SERVER_MANAGER` or `marv run --server-manager marv-mlx`.

The TUI status bar shows the last turn's time-to-first-token and prefill cost
(`ttft <ms>/<tokens>t`); `marv bench` (see below) reports the aggregate.

`AGENT_LLM_READ_TIMEOUT` caps the streaming read timeout for any provider
(seconds, `off`/`0` disables); local backends wait indefinitely by default so a
long prefill is not killed.

A commented template ships at `config/default.toml`. The default provider is
`marv-mlx` on `http://localhost:11500` and needs no API key.

## Layout

`runtime/` (agent loop, sessions, compaction) → `llm/` (provider adapters) →
`config/`, `tools/`, `skills/`, `prompts/`, `extensions/` → delivery (`tui/`,
`cli/`). See [`docs/architecture.md`](docs/architecture.md) for the module
layout and responsibilities.

## Development

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for the dev setup and `make` targets.

Measure local latency with `marv bench` — a scripted multi-turn conversation
through the real agent loop that reports TTFT p50/p90 and how much TTFT grows
per turn (`--json` for machine consumption); it also seeds the per-model
calibration that drives the wait estimate:

```sh
uv run marv bench
uv run marv bench -n 8 --json
```

## License

MIT.