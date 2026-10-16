### Changed

- **`@` completion lists only files that can be attached.** The dropdown now
  offers directories (for descent) and files whose suffix is an image or `pdf`
  — the same set the attachment parser accepts — instead of every entry in the
  directory, so a stray source file is no longer a suggestion. A partial that
  matches no attachable file leaves the dropdown hidden.
