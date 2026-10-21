### Added

- **Kurt mode, for now a chat-like mode.** `/kurt` turns every tool off, sends no
  tool schemas, and shows `🤘 kurt mode - tools off / kurt on`; the status bar
  marks it `[kurt]`. It behaves as plain chat until kurt's own behaviour lands.

### Changed

- **Emoji status rows.** A warmed model settles as `🚀 all systems ready`, and
  mode switches announce themselves with `🛠️` (tools), `💬` (chat), and `🤘`
  (kurt). Mode announcements are bare emoji rows, without the historical `--`.
- **Wait narration names only the new tokens.** With a cached prefix the
  indicator reads `adding 16 tokens (2.712k cached)` instead of re-prefilling
  the whole prompt. `taking longer than predicted` now waits for a ten-second
  overrun instead of firing on the first second past the estimate.

### Fixed

- **Code blocks are framed, not followed by a frame.** A code fence no longer
  reserves an empty horizontal scrollbar row beneath the code, and the ANSI
  themes now give the fence an opaque background so the code sits inside it.
