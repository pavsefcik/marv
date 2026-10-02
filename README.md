# marv

A YMLX-powered coding agent TUI for Apple Silicon. **marv** is a lean, hackable
agent harness that runs entirely on local MLX models via
[YMLX](https://github.com/pavsefcik/ymlx) — no cloud accounts, no API keys.

It is a fork of
[eddmann/my-own-coding-agent](https://github.com/eddmann/my-own-coding-agent)
(MIT), stripped down to a single local backend and rebranded:

- **Removed:** `web/` FastAPI delivery, the OpenAI / Anthropic / OpenAI-Codex
  cloud providers, OAuth flows, and pricing tables.
- **Added:** a YMLX provider (local MLX models), ymlx-aware model discovery and
  server lifecycle, and family-accurate thinking support.
- **Kept:** the readable agent loop, Textual TUI, skills, prompt templates,
  extensions, JSONL sessions (fork/resume), context compaction, and the
  read/write/edit/bash tool suite.

## Why this exists

To own the whole agent harness end-to-end — and to run it on the models you
already manage locally with ymlx. It is not the best agent; it is _readable,
hackable, and yours_.

## Requirements

- Apple Silicon Mac
- Python 3.14+ and `uv`
- [YMLX](https://github.com/pavsefcik/ymlx) installed (manages the MLX models
  and serves them on `localhost:11500`)

## Quickstart

```sh
make deps      # uv sync
make run       # start the TUI (picks up the running ymlx model, or pick one)
```

Headless (single prompt against a running ymlx model):

```sh
make run-headless PROMPT="List all Python files"
uv run marv run --headless -m mlx-community/Qwen3.5-4B-MLX-4bit "Say hi"
```

`make run` connects to YMLX. If a ymlx server is already running, marv targets
that model; otherwise the model picker lists every model ymlx manages (from the
local HF hub) and selecting one starts/swaps it via `ymlx run <id>`.

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

State (sessions, skills, prompt templates) lives under `~/.cache/marv/`.

A commented template ships at `config/default.toml`. The default provider is
`ymlx` at `http://localhost:11500`; no API key is needed.

## The agent loop

1. Input intake & preprocessing — slash commands, skills, input extensions.
2. Session + context guardrails — persist to JSONL, compact if needed.
3. Prompt construction & model stream — provider streams text/thinking/tools.
4. Tool execution cycle — calls parsed, validated, executed, results appended.
5. Turn finalization — events emitted, extension messages drained, token stats.

## Layout

```
runtime/     Agent loop, sessions, context compaction, prompts
llm/         Provider adapters (YMLX + OpenAI-compatible) + streaming events
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