### Changed

- **Large image attachments are downscaled at attach time.** An image over
  1.5 MB is scaled to 1568 px on the long edge with macOS `sips` before it is
  base64-encoded, so a multi-megapixel photo no longer inflates the session
  JSONL and every turn's prefill with pixels the vision encoder discards.
  Best-effort: a machine without `sips` (or an engine failure) sends the
  original bytes, and the 8 MiB hard cap still applies to the result.
