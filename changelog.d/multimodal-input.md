### Added

- **Multimodal input.** Send an image (or page 1 of a PDF) to a vision-capable
  model. `Message.content` stays a string; images ride in a new
  `Message.attachments` field of `ImageAttachment`s, and `to_api_dict()` emits
  OpenAI content parts (`text` + `image_url` data URLs) for a user message that
  carries them. The provider protocol gains `supports_vision()`
  (`OpenAICompatibleProvider`/`marv-mlx`: true — `mlx_vlm` is vision-capable;
  `apple-fm`: false), and a text-only backend still runs the turn, dropping the
  images with a system warning. `count_messages_tokens` charges a per-image
  `IMAGE_TOKEN_COST` budget rather than tokenizing the base64 payload.
- **`@path` attachments in the TUI.** Typing `@path` (or `@"path with spaces"`)
  attaches an image or PDF page, strips the token from the prompt, and shows a
  dim `[image: name]` line that is restored when a session is replayed. Anything
  that is not an existing image or PDF stays as ordinary text. Loading runs off
  the UI thread; a failure aborts the turn with a system message.
- **`--attach` in headless mode.** A repeatable `marv run --headless --attach
  PATH` flag, plus `@path` tokens found in the prompt, send the same attachments
  through `run_headless` → `Agent.run`.
- **PDF page-1 rendering** (`src/marv/runtime/attachments.py`) via `pdftoppm`
  (poppler) or macOS `sips`, with a clear "install poppler" error when neither
  is available. An attachment over 8 MiB is rejected with a resize hint. No new
  Python dependency.
