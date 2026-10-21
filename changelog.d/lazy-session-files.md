### Changed

- **Sessions are saved only once they have a prompt and an answer.** A launch,
  a `/resume` detour, or a prompt that never gets a reply stays in memory, so
  `~/.cache/marv/sessions` no longer fills up with empty or abandoned JSONL
  files. The first answer writes the whole conversation so far, including the
  system prompt and any earlier model changes.
- **`/resume` lists every completed session.** Greetings and short tests are no
  longer filtered out; a session only needs at least one prompt and one answer.
  Sessions are described by their first prompt.
