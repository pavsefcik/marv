"""Behaviour tests for the office-files skill that ships with marv."""

from __future__ import annotations

from pathlib import Path

from marv.skills.loader import SkillLoader
from marv.skills.skill import Skill, SkillSource
from marv.skills.validator import validate_skill

BUILTIN_DIR = Path(__file__).resolve().parents[2] / "src" / "marv" / "skills" / "builtin"
OFFICE_FILES_DIR = BUILTIN_DIR / "office-files"


def test_office_files_skill_loads_with_name_matching_directory():
    loader = SkillLoader([(BUILTIN_DIR, SkillSource.BUILTIN)])

    skill = loader.get("office-files")

    assert skill is not None
    assert skill.name == "office-files"
    assert skill.readme_path == OFFICE_FILES_DIR / "SKILL.md"
    assert skill.readme_path.parent.name == skill.name
    assert skill.description.strip()
    assert skill.readme_content.strip()
    assert loader.diagnostics.error_count == 0
    assert loader.diagnostics.collision_count == 0


def test_office_files_skill_passes_validator():
    skill = Skill.from_markdown(OFFICE_FILES_DIR / "SKILL.md", source=SkillSource.BUILTIN)

    result = validate_skill(skill.name, skill.description, skill.readme_path)

    assert result.valid, [error.message for error in result.errors]


def test_office_files_skill_checks_for_pdf_engines_before_using_them():
    skill = Skill.from_markdown(OFFICE_FILES_DIR / "SKILL.md", source=SkillSource.BUILTIN)

    assert "command -v" in skill.readme_content
    for binary in ("sips", "pdftoppm", "gs", "qpdf"):
        assert binary in skill.readme_content
