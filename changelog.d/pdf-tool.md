### Added

- **`pdf` tool.** One tool that does real PDF work by driving external engines
  instead of adding a Python dependency: `info` and `text` (poppler), `render`
  (`pdftoppm`), `compress` (Ghostscript), and `merge`, `split`, `rotate`
  (qpdf). A missing engine fails with the binary and the exact install command
  (`` `pdftoppm` not found. Install poppler: brew install poppler ``).
  Writing actions never overwrite the input, default to a sibling file, verify
  the engine really wrote its output, and report the produced files as
  path + size. Under `approval_mode=destructive` every writing action needs
  approval; `info` does not, and `text` needs it only when it writes.
- **`marv doctor`: `pdf tooling`.** Reports which of the five engines the `pdf`
  tool drives are present, with their paths, and which are missing, with the
  Homebrew command that installs them. `ok` when all are present, `warn`
  otherwise — PDF work is optional, so a bare machine is not a failure.
- **`docs/tools.md`.** Documents the `pdf` schema, the command each action runs,
  and the rules the tool enforces.
