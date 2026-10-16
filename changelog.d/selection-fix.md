### Fixed

- **Selecting part of an answer.** An answer rendered as a Rich `Markdown`
  renderable, which carries no content offsets, so Textual had no cell to anchor
  a selection to and any drag over a reply selected — and `Ctrl+C` copied — the
  whole reply. Answers now render through Textual's own `Markdown` widget, so
  dragging over part of an answer selects exactly the text swept over: one line,
  a wrapped paragraph, or a range across paragraphs. Prompts, attachment lines,
  and the answer's markdown styling (headings, lists, code, bold) are unchanged,
  and `Ctrl+O` remains the whole-reply fallback.
