### Added

- **PDF page selection.** `@report.pdf:3` attaches one page and
  `@report.pdf:2-4` attaches a range, in the TUI and in the repeatable
  `marv run --attach` flag. Chosen pages render with `pdftoppm`, one attachment
  per page (named `report.pdf p.3`), up to 10 per attachment. Requesting any
  page beyond 1 without `pdftoppm` fails with the `brew install poppler` hint
  instead of silently sending page 1; the macOS `sips` fallback still renders
  page 1.

### Changed

- **Attachments are stored on disk, not in the session.** `ImageAttachment.data`
  (base64) is excluded from serialization; each blob is written content-addressed
  by sha256 under the session's `attachments/` directory and referenced by a
  `ref` filename. A session JSONL no longer carries megabytes of image bytes,
  and a resumed session reads the blob back before the turn. Identical bytes
  deduplicate to one file, and a missing blob degrades to no image rather than
  failing the turn.
