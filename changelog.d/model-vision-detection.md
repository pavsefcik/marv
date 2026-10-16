### Changed

- **Vision support is detected per local MLX model.** `marv-mlx` no longer
  claims every model accepts images: `model_supports_vision` reads the local hub
  snapshot (image-processor config, a vision marker in `config.json`, or a
  VLM-shaped model name). A text-only MLX model now drops `@path` images with a
  system warning instead of failing at the server, and a model with no local
  snapshot stays permissive on purpose so an image is never silently dropped.
  `marv doctor` gains a `vision` check for the active provider and model.
