# marv — a YMLX-powered coding agent TUI
# Add this line to ~/.zshrc:
#   source "/path/to/marv-launcher.zsh"
# Then reload your shell:  source ~/.zshrc
#
# The launcher runs the marv CLI from this repo. It prefers the repo's own
# `.venv/bin/marv` (created by `make deps` / `uv sync`), and falls back to
# `uv run` so a freshly-cloned checkout still works without syncing first.

_MARV_DIR="${0:A:h}"

if [[ -x "$_MARV_DIR/.venv/bin/marv" ]]; then
  marv() { "$_MARV_DIR/.venv/bin/marv" "$@"; }
else
  marv() { (cd "$_MARV_DIR" && uv run marv "$@"); }
fi