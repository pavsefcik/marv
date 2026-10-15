### Changed

- **Startup hint names the useful controls.** The header's second line now reads
  `Type /help for commands · escape interrupt · ctrl+l clear · ctrl+o copy reply`
  instead of advertising `shift+enter`, which the prompt placeholder already
  explains.
- **`/help` explains the commands.** It prints a block with one line per command
  (`/compact    summarise old context on demand`) and the keys bound outside the
  prompt, instead of one pipe-separated list.
- **Transient status rows lead with `✓`.** A settled notice reads `✓ model ready`
  rather than `-- model ready`; the `--` marker stays for permanent, historical
  system messages. The spinner form is unchanged.

### Fixed

- **Answers can be selected and copied.** Clicking an answer no longer copies the
  whole message to the clipboard, which made mouse selection useless for a long
  reply. Dragging selects text (`Ctrl+C` copies it) and `Ctrl+O` still copies the
  whole last reply. Answers are rendered from Markdown, which Textual's default
  selection cannot read, so the message widget now extracts the selected text
  from the lines it renders.
- **User prompts are marked by a stripe.** A prompt carries a `$primary` border
  down its left edge, so it cannot be mistaken for an answer. It is a border, not
  a background, because the ANSI themes' `$surface`/`$panel` are transparent.
- **The prompt placeholder is muted.** The placeholder rule never applied — a `>`
  combinator cannot match a Textual component class — so the selector is a
  descendant selector and the theme's disabled text colour now wins over
  TextArea's own default. Under the ANSI themes, where `$text-disabled` has no
  alpha and collapses to the terminal's default (white) foreground, an `:ansi`
  rule pins the placeholder to bright black so it is visibly grey there too.
