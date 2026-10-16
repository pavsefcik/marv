### Fixed

- **System-message text is escaped.** A system row (including `!command` shell
  output) is rendered literally now, so square brackets in command output can no
  longer be parsed as Rich markup or raise a markup error.
