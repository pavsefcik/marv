"""Behaviour tests for the skills that ship inside the package."""

from __future__ import annotations

from pathlib import Path

import marv.skills
from marv.skills.loader import SkillLoader
from marv.skills.skill import Skill, SkillSource

BUILTIN_DIR = Path(marv.skills.__file__).resolve().parent / "builtin"
OFFICE_FILES_SKILL = BUILTIN_DIR / "office-files" / "SKILL.md"


def write_skill(dir_path: Path, name: str, description: str) -> None:
    skill_dir = dir_path / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n"
    )


def test_default_loader_discovers_builtin_office_files(temp_dir, monkeypatch):
    monkeypatch.setenv("HOME", str(temp_dir / "home"))

    loader = SkillLoader.with_defaults(cwd=temp_dir / "project")

    skill = loader.get("office-files")

    assert skill is not None
    assert skill.source is SkillSource.BUILTIN
    assert skill.readme_path == OFFICE_FILES_SKILL
    assert loader.diagnostics.loaded_count == 1
    assert loader.diagnostics.collisions == []
    assert not loader.diagnostics.has_issues()


def test_default_loader_discovers_builtin_office_files_from_any_cwd(temp_dir, monkeypatch):
    monkeypatch.setenv("HOME", str(temp_dir / "home"))

    for cwd in (temp_dir / "one", temp_dir / "two" / "nested"):
        skill = SkillLoader.with_defaults(cwd=cwd).get("office-files")

        assert skill is not None
        assert skill.source is SkillSource.BUILTIN


def test_builtin_office_files_still_loads_when_the_user_dir_has_other_skills(temp_dir, monkeypatch):
    home = temp_dir / "home"
    monkeypatch.setenv("HOME", str(home))
    write_skill(home / ".marv" / "skills", "deploy", "user deploy")

    loader = SkillLoader.with_defaults(cwd=temp_dir / "project")

    skill = loader.get("office-files")
    assert skill is not None
    assert skill.source is SkillSource.BUILTIN
    assert loader.get("deploy") is not None
    assert loader.diagnostics.collision_count == 0
    assert loader.diagnostics.error_count == 0


def test_user_skill_overrides_builtin_office_files_and_records_the_collision(temp_dir, monkeypatch):
    home = temp_dir / "home"
    monkeypatch.setenv("HOME", str(home))
    write_skill(home / ".marv" / "skills", "office-files", "my own office playbook")

    loader = SkillLoader.with_defaults(cwd=temp_dir / "project")

    skill = loader.get("office-files")
    assert skill is not None
    assert skill.source is SkillSource.USER
    assert skill.description == "my own office playbook"

    collisions = loader.diagnostics.collisions
    assert len(collisions) == 1
    assert collisions[0].kept_source is SkillSource.USER
    assert collisions[0].kept_path == home / ".marv" / "skills" / "office-files" / "SKILL.md"
    assert collisions[0].discarded_source is SkillSource.BUILTIN
    assert collisions[0].discarded_path == OFFICE_FILES_SKILL


def test_project_skill_overrides_builtin_office_files_and_records_the_collision(temp_dir):
    cwd = temp_dir / "project"
    write_skill(cwd / ".marv" / "skills", "office-files", "project office playbook")
    write_skill(cwd / ".marv" / "skills", "deploy", "project deploy")

    loader = SkillLoader.with_defaults(cwd=cwd)

    skill = loader.get("office-files")
    assert skill is not None
    assert skill.source is SkillSource.PROJECT
    assert skill.description == "project office playbook"

    collisions = loader.diagnostics.collisions
    assert len(collisions) == 1
    assert collisions[0].kept_source is SkillSource.PROJECT
    assert collisions[0].discarded_source is SkillSource.BUILTIN
    assert collisions[0].discarded_path == OFFICE_FILES_SKILL


def test_builtin_office_files_body_is_the_office_playbook():
    skill = Skill.from_markdown(OFFICE_FILES_SKILL, source=SkillSource.BUILTIN)

    assert skill.name == "office-files"
    assert skill.description.strip()
    body = skill.read_body()
    assert body.strip()
    assert "sips" in body
