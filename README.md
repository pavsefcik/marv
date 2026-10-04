# marv

A local-first coding agent TUI for Apple Silicon. **marv** is a lean, hackable
agent harness that runs entirely on-device — no cloud accounts, no API keys.

Backends:

- **`marv-mlx`** (default) — marv launches [`mlx-vlm`](https://github.com/Blaizzy/mlx-vlm)'s
  `mlx_vlm.server` itself and runs any MLX model downloaded in the local Hugging
  Face hub, with family-accurate thinking support. Models can be downloaded and
  managed with [YMLX](https://github.com/pavsefcik/ymlx) — marv reads the same
  hub but has **no runtime dependency** on it.
- **`apple-fm`** — Apple's built-in Foundation Model via the macOS 27+ `fm` CLI
  (`system` on-device, or `pcc` on Private Cloud Compute). Chat-only: the
  endpoint has no function/tool calling.

It is a fork of
[eddmann/my-own-coding-agent](https://github.com/eddmann/my-own-coding-agent)
(MIT), stripped down to local backends and rebranded:

- **Removed:** `web/` FastAPI delivery, the OpenAI / Anthropic / OpenAI-Codex
  cloud providers, OAuth flows, and pricing tables.
- **Added:** the `marv-mlx` and `apple-fm` backends, self-managed server
  lifecycle (start/swap/unload, including on crash), and family-accurate
  thinking support.
- **Kept:** the readable agent loop, Textual TUI, skills, prompt templates,
  extensions, JSONL sessions (fork/resume), context compaction, and the
  read/write/edit/bash tool suite.

## Why this exists

To own the whole agent harness end-to-end — and to run it on the models you
already have locally. It is not the best agent; it is _readable, hackable, and
yours_.

## Requirements

- Apple Silicon Mac
- Python 3.14+ and `uv`
- For `marv-mlx`: the `mlx-vlm` tool — `uv tool install mlx-vlm --with jinja2 --with setproctitle`
  (the `setproctitle` extra is optional and only affects the process name)
- For `apple-fm`: macOS 27+ with the `fm` CLI licensed (`sudo fm license`)

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
uv run marv run --headless -p apple-fm "Say hi"
```

marv starts the model server itself and stops it when it exits. The model picker
lists every MLX model downloaded in `~/.cache/huggingface/hub` (or the `system` /
`pcc` models for `apple-fm`), and selecting one starts/swaps the server. If a
model is already selected — from config, a resumed session, or the last-used
state — marv starts its server on launch instead of waiting for the first prompt.

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

A commented template ships at `config/default.toml`. The default provider is
`marv-mlx` on `http://localhost:11500`; `apple-fm` uses
`http://127.0.0.1:1976`. Neither needs an API key.

## The agent loop

1. Input intake & preprocessing — slash commands, skills, input extensions.
2. Session + context guardrails — persist to JSONL, compact if needed.
3. Prompt construction & model stream — provider streams text/thinking/tools.
4. Tool execution cycle — calls parsed, validated, executed, results appended.
5. Turn finalization — events emitted, extension messages drained, token stats.

## Layout

```
runtime/     Agent loop, sessions, context compaction, prompts
llm/         Provider adapters (mlx-vlm + Apple FM + OpenAI-compatible) + streaming events
config/      Runtime config loading
tools/       Built-in tool registry + implementations
skills/      Skill discovery + validation
prompts/     Prompt templates + argument expansion
extensions/  Event hooks + runtime/session/model/tool/UI host
tui/         Textual UI (interactive mode)
cli/         Typer command surface + headless/session helpers
```

## Development

```sh
make test
make lint
make format
make can-release   # lint + tests
```

## License

MIT. Derived from [my-own-coding-agent](https://github.com/eddmann/my-own-coding-agent)
by eddmann.