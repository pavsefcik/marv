# Changelog

All notable changes to marv are documented here. The version in
[`VERSION`](VERSION) is the single source of truth.

## [0.103.0] - 2025-10-04

### Added

- **Tool approval mode** (`approval_mode` / `AGENT_APPROVAL` / `marv run --approval`).
  `destructive` asks before `write`/`edit` and risky shell commands; `all` asks
  before every tool call. Off by default. In the TUI it uses a Yes/No confirmation;
  headless denies an unapproved tool with a message.
- `marv --version`.
- A `Release` workflow that verifies `VERSION` against the tag, builds the
  wheel/sdist, smoke-tests the install, and attaches artifacts to the release.
- A `build` CI job that builds and smoke-tests the wheel on every push/PR.
- `CONTRIBUTING.md`, `SECURITY.md`, issue/PR templates, and Dependabot config.

### Changed

- Filled out `pyproject.toml` metadata (license, authors, keywords, classifiers,
  project URLs).
- Fixed stale documentation: old `agent.*` module names, `uv run agent run`
  examples, example config paths, and a sample session provider. `PLAN.md` is now
  marked as an archived historical document.

## [0.102.0] - 2025-10-04

### Changed

- Default branch is now `main` (was `master`), so the CI workflow in
  `.github/workflows/` runs on push and pull request as intended.

## [0.101.0] - 2025-10-04

First tagged release.

### Added

- **Last-used selection is remembered across restarts.** The active
  provider/model/thinking level is persisted to `~/.cache/marv/state.toml` and
  reapplied on the next start, so the model picker no longer blocks startup.
  Explicit config files, `AGENT_*` env vars, and CLI flags still take
  precedence over the remembered value.
- **The model server starts on launch.** When a model is already selected
  (remembered, from config, or resumed), marv warms its local server
  immediately instead of waiting for the first prompt.
- `VERSION` file as the single version source, wired into the package metadata
  via hatchling.

### Notes

- Local-first backends only: `marv-mlx` (default) and `apple-fm`.

[0.103.0]: https://github.com/pavsefcik/marv/releases/tag/v0.103.0
[0.102.0]: https://github.com/pavsefcik/marv/releases/tag/v0.102.0
[0.101.0]: https://github.com/pavsefcik/marv/releases/tag/v0.101.0
