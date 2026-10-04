# Changelog

All notable changes to marv are documented here. The version in
[`VERSION`](VERSION) is the single source of truth.

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

[0.102.0]: https://github.com/pavsefcik/marv/releases/tag/v0.102.0
[0.101.0]: https://github.com/pavsefcik/marv/releases/tag/v0.101.0
