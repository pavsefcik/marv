# AGENTS.md — marv

Project instructions for coding agents. `CLAUDE.md` is a symlink to this file.

## What this is

marv is a local-first coding agent TUI for Apple Silicon. It runs entirely
on-device — no cloud accounts, no API keys. Python 3.14 + `uv` + Textual.
Fork of `eddmann/my-own-coding-agent`.

Backends:

- `marv-mlx` (default): marv spawns `mlx_vlm.server` and runs any MLX model in
  the local Hugging Face hub.
- `apple-fm`: Apple's Foundation Model via the macOS 27+ `fm` CLI. Chat-only
  (no tool calling).

## Commands

Prefer Makefile targets over direct commands. Run `make help` to list them.

- `make deps` — install deps. Also reinstalls the local package, which is needed
  after a `VERSION` bump.
- `make test` / `make lint` / `make format`
- `make can-release` — lint + tests. **Run before wrapping up substantial changes.**
- `make run` — TUI; `make run-headless PROMPT="..."` — one-shot.

## Layout

- `src/marv/llm/` — provider adapters + streaming events (`marv_mlx.py`,
  `apple_fm.py`, `local_server.py`, `factory.py`)
- `src/marv/runtime/` — agent loop, sessions, context compaction, approval
  policy, hooks
- `src/marv/tools/` — read/write/edit/bash/grep/find/ls + registry
- `src/marv/config/` — config loading and last-used state
- `src/marv/tui/` — Textual UI; `src/marv/cli/` — Typer surface + headless
- `src/marv/extensions/`, `skills/`, `prompts/`
- `docs/` — source of truth for behaviour. `PLAN.md` is archived/historical.
- `tests/` — mirrors `src/`, with fakes in `tests/test_doubles/`.

Layering is `llm/` → `runtime/` → delivery (`tui/`, `cli/`). Do not leak
delivery concerns into `runtime/`.

## Conventions

- **Detroit-style tests**: assert on observable behaviour and mock only at the
  boundary (`LLMProviderFake` is the main seam). A behaviour change needs a test
  that fails before and passes after.
- `ruff` (line length 100) + `mypy --strict`; both run in `make lint`.
- Python 3.14 idioms: `StrEnum`, `X | None`, `match`.

## Invariants & gotchas

- `VERSION` is the single source of truth. `pyproject.toml` reads it via
  hatchling and `marv.__version__` comes from package metadata. After editing
  `VERSION`, run `make deps` or the version test fails.
- Default branch is `main`. CI is `.github/workflows/test.yml` (test + build
  jobs); `.github/workflows/release.yml` runs on published releases.
- `approval_mode` (`off`/`destructive`/`all`) defaults to `off`, so the default
  path is unchanged. It is a guardrail, not a sandbox: tools run with the user's
  full permissions. In headless mode an unapproved tool is denied, not run.
- The last-used provider/model/thinking is persisted to `state.toml` beside the
  session dir and is the weakest config layer (explicit config/env/CLI win).
- When a model is already selected, its server auto-starts on launch.
- `Agent.set_model` rejects invalid provider/model pairs; thinking levels are
  clamped to model capability.

## Releasing

1. Edit `VERSION` and add a `CHANGELOG.md` entry.
2. `make deps` (refresh metadata) and `make can-release`.
3. Commit, push `main`, then create the release with a tag matching `VERSION`:
   `gh release create v<VERSION> --target main --title v<VERSION> --notes ...`.
   The Release workflow verifies `VERSION` against the tag, builds, smoke-tests
   the wheel, and attaches the wheel/sdist.

## Security

Tools are unsandboxed and extensions are arbitrary code. See `SECURITY.md`.
