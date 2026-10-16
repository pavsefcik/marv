### Added

- **`!command` in the TUI.** A submitted prompt starting with `!` runs the rest
  through the agent's `bash` tool and prints the output in the chat instead of
  sending it to the model. It is an explicit user action, so it skips the
  approval policy; `!` alone does nothing. `/help` lists it.
- **`--attach` in the interactive TUI.** The repeatable `--attach PATH` flag was
  honored headless only; a TUI launched with it now attaches those images (and
  page-1 PDFs) to the first submitted message, ahead of any `@path` tokens, then
  clears them. The no-prompt path keeps the same validation and error as
  headless.
- **`marv doctor` reports the update check.** The verdict says whether
  `update_check` is on and compares the cached latest release with the running
  version — reading the cache only, so doctor never touches GitHub. `warn` when
  a newer release is cached, `ok` when up to date or disabled.

### Fixed

- **A chosen theme is no longer wiped on the next start or model switch.** The
  TUI's `remember_selection` built a fresh state object and saved it, dropping
  the persisted theme (and every model's latency fit). It now merges through
  `marv.config.state.remember_selection`, so a restart applies the theme again.
