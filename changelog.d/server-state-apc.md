## [Unreleased]

### Fixed

- The `_proctitle` sitecustomize hook now chain-loads the next `sitecustomize`
  on `sys.path`. Python imports only the first one, and the hook directory sits
  on `PYTHONPATH` ahead of site-packages, so it silently shadowed the marv-mlx
  APC (prefix-cache) defaults installed in the mlx-vlm tool env — every
  marv-launched server ran with prefix caching off and re-prefilled the whole
  conversation every turn (`cached_tokens=0`, 8-13 s prefills on long contexts).
  Embedded launches now log `APC enabled` and reuse cached prefixes.

### Added

- The wait prediction reads live state from the serving mlx_vlm server
  (`/v1/metrics`, via the new `marv.llm.server_metrics`). Server-measured
  turns seed the prefill-rate calibration before marv has any samples of its
  own, so a first-ever run gets a real ETA instead of none; a persisted
  calibration is never overwritten.
- `WaitEstimate` now carries `cached_tokens`: when the server reports prefix
  caching enabled, the prediction prices only the tokens added since the
  previous turn instead of the full prompt, and the TUI wait narration shows
  the cached share ("prefilling 1.2k tokens (9.8k cached)"). Compaction and
  model switches reset the expected prefix.
