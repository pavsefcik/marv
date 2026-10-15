# Skills

Skills are Markdown documents with YAML frontmatter. They are loaded from multiple locations and can be injected into the prompt with `$skill-name`.

## Skill format

```markdown
---
name: my-skill
description: What this skill does
# optional
# disable_model_invocation: true
---

# My Skill

Instructions and references...
```

## Discovery order (low → high priority)

1. `src/marv/skills/builtin/` (ships with marv)
2. `~/.marv/skills/`
3. Any `skills_dirs` configured in `config.toml`
4. `.marv/skills/` (project‑local)

If there are name collisions, higher‑priority sources win, so a user, config or
project skill always overrides a builtin one with the same name.

## Validation rules

- lowercase letters, numbers, and hyphens only
- must match directory name
- name max length 64 chars; description max length 1024 chars
- description required

## Invocation

- `$skill-name` injects the skill body into the prompt.
- `$skill-name extra args...` appends args after the injected block.

## Examples

- [`examples/skills/repo-scan/SKILL.md`](../examples/skills/repo-scan/SKILL.md) — fast codebase orientation.
- [`examples/skills/safe-refactor/SKILL.md`](../examples/skills/safe-refactor/SKILL.md) — refactor checklist.

marv also ships one skill: [`src/marv/skills/builtin/office-files/SKILL.md`](../src/marv/skills/builtin/office-files/SKILL.md) — the office document/image playbook ([`docs/office.md`](office.md)), available as `$office-files` with no setup.

The example skills are not loaded by default: copy one into `~/.marv/skills` or
`.marv/skills`, or add its directory to `skills_dirs` in config.
