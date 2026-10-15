### Added

- **`office-files` now ships with marv.** The office playbook moved from
  `examples/skills/office-files/` into the package at
  `src/marv/skills/builtin/office-files/SKILL.md`, and the default skill loader
  always searches the packaged `builtin/` directory. A fresh `Agent` therefore
  sees `office-files` with no setup — `$office-files` works on first launch,
  with no copy into `~/.marv/skills` and no `skills_dirs` entry.

### Changed

- **`SkillSource.BUILTIN` is the lowest-priority skill source.** The discovery
  order is now builtin → user (`~/.marv/skills`) → config (`skills_dirs`) →
  project (`.marv/skills`), so a user, config, or project skill with the same
  name overrides the packaged one and the override is recorded as a collision in
  the loader diagnostics.
