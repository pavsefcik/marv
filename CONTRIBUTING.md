# Contributing to marv

Thanks for taking a look. marv is a small, readable agent harness — the goal is to
keep it that way.

## Requirements

- Apple Silicon Mac (the `marv-mlx` and `apple-fm` backends are macOS-only)
- Python 3.14+ and [`uv`](https://docs.astral.sh/uv/)
- For `marv-mlx`: `uv tool install mlx-vlm --with jinja2 --with setproctitle`
- For `apple-fm`: macOS 27+ with `sudo fm license` accepted

## Getting started

```sh
make deps     # install dependencies
make test     # run the test suite
make lint     # ruff + mypy
make can-release   # lint + tests (run before opening a PR)
make run      # launch the TUI
```

Prefer the Makefile targets over raw commands so CI and local runs stay in sync.

## Tests

Tests live under `tests/` and are organized by layer (`config/`, `runtime/`,
`tools/`, `llm/`, `delivery/`, `extensions/`).

Follow the existing **Detroit-style** approach: assert on observable behaviour and
mock only at the boundary (the fake LLM provider in `tests/test_doubles/` is the
main seam). Avoid asserting on private internals when a public behaviour will do.

Every behaviour change should come with a test that fails before the change and
passes after.

## Code style

- `ruff` for lint + format, `mypy --strict` for types. Both run in `make lint`.
- Keep modules focused; the layering is `llm/` → `runtime/` → delivery (`tui/`,
  `cli/`). Delivery concerns should not leak into `runtime/`.
- Prefer small, pure helpers that are easy to test.

## Versioning

`VERSION` is the single source of truth; `pyproject.toml` reads it via hatchling
and `marv.__version__` comes from package metadata. To bump: edit `VERSION`, add a
`CHANGELOG.md` entry, then `make deps` to refresh the local install.

## Releases

Releases are cut from `main` by publishing a GitHub Release whose tag is
`v<VERSION>`, e.g. `v0.104.0` (the tag can be created as part of publishing).
Publishing triggers the `Release` workflow, which verifies the tag matches
`VERSION`, builds the wheel/sdist, smoke-tests the built wheel, and attaches the
artifacts to the release. Pushing a tag on its own does **not** trigger it — the
workflow listens for `release: published`.

## Pull requests

- Keep the change focused; unrelated refactors belong in their own PR.
- Make sure `make can-release` is green.
- Describe the behaviour change and how you verified it.
