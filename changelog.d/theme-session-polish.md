### Changed

- **The marv theme blends into the terminal.** It is now an ANSI-aware theme that
  uses the same terminal-native background as `ansi-dark` instead of painting a
  black box behind the TUI; the magenta/grey marv palette is unchanged.
- **Session resume lists only sessions worth returning to.** `/resume` and
  `marv sessions` skip launch-created empty sessions and one-off greetings/tests
  (`hi`, `testing`, `thanks`), and each entry starts with a one-line description
  taken from the first meaningful prompt.

### Fixed

- **Code fences keep their spacing under the ANSI themes.** Textual's ANSI
  variant collapsed the fence margin and side padding, so code blocks and the
  headings around them sat differently from the marv and hex themes. The frame
  geometry is now the same in every theme.
