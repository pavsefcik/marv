# Changelog fragments

Agents and contributors write **one fragment per change** here instead of
editing the top of `CHANGELOG.md`. Editing a shared version heading from two
concurrent writers is the failure this prevents: two sections and a half-bumped
`VERSION` landing in one working copy.

## Convention

- File name: `<slug>.md` (short, kebab-case, describing the change), e.g.
  `uninstall-picker.md`.
- Content: Keep a Changelog headings and bullets only — no version heading, no
  date, no `VERSION` edit. Start at `### Added` / `### Changed` / `### Fixed`.

```markdown
### Added

- **`marv frobnicate`.** One-line description, then the observable behaviour.
```

## At release

The releaser folds every fragment into `CHANGELOG.md` under the new version
heading, deletes the fragment files, bumps `VERSION`, and tags. Nothing in this
directory survives a release.

`make doctor` fails if `CHANGELOG.md` has more than one `## [Unreleased]`
heading, which is the signature of two writers sharing a tree.
