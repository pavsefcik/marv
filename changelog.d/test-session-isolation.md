### Fixed

- **Tests no longer write sessions into the real `~/.cache/marv`.** The CLI
  headless tests built real agents under the developer's home directory, so
  their `/dump-config` prompts appeared as resumable entries in `/resume`. Every
  test now runs with an isolated `HOME`.
