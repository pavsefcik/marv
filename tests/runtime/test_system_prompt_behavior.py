"""Behavior tests for system prompt assembly."""

from __future__ import annotations

from marv.runtime.prompt_builder import ContextFile, SystemPromptOptions, build_system_prompt
from marv.skills.skill import Skill, SkillSource


def test_system_prompt_sections_in_order(temp_dir):
    cwd = temp_dir / "work"
    context_file = ContextFile(path=cwd / "AGENTS.md", content="ctx content", source="project")
    skill = Skill(
        name="deploy",
        description="deploy app",
        readme_path=cwd / "skills" / "deploy" / "SKILL.md",
        readme_content="Skill body",
        base_dir=cwd,
        source=SkillSource.PROJECT,
    )

    options = SystemPromptOptions(
        custom_prompt="custom",
        selected_tools=["read", "edit", "grep", "find", "ls"],
        append_system_prompt="append",
        cwd=cwd,
        context_files=[context_file],
        skills=[skill],
    )

    prompt = build_system_prompt(options)

    sections = [
        "custom",
        "Available tools:",
        "Guidelines:",
        "## Project Context (AGENTS.md)",
        "<available_skills>",
        "Environment:",
        "append",
    ]

    indices = [prompt.find(section) for section in sections]
    assert all(index >= 0 for index in indices)
    assert indices == sorted(indices)

    assert "- read: Read file contents with line numbers" in prompt
    assert "When exploring files" in prompt
    assert "Working Directory" in prompt


def test_plain_chat_prompt_is_not_a_coding_agent(temp_dir):
    cwd = temp_dir / "work"
    context_file = ContextFile(path=cwd / "AGENTS.md", content="ctx content", source="project")
    skill = Skill(
        name="deploy",
        description="deploy app",
        readme_path=cwd / "skills" / "deploy" / "SKILL.md",
        readme_content="Skill body",
        base_dir=cwd,
        source=SkillSource.PROJECT,
    )

    options = SystemPromptOptions(
        selected_tools=["read", "bash"],
        cwd=cwd,
        context_files=[context_file],
        skills=[skill],
        plain_chat=True,
    )

    prompt = build_system_prompt(options)

    assert "helpful, friendly assistant" in prompt
    assert "coding assistant" not in prompt
    assert "Available tools:" not in prompt
    assert "## Project Context" not in prompt
    assert "<available_skills>" not in prompt
    assert str(cwd) not in prompt


def test_system_prompt_omits_tools_for_a_tool_less_provider(temp_dir):
    options = SystemPromptOptions(
        selected_tools=["read", "bash"],
        cwd=temp_dir,
        tools_available=False,
    )

    prompt = build_system_prompt(options)

    assert "Available tools:" not in prompt
    assert "- read: Read file contents" not in prompt
    assert "cannot read files, run commands, or apply edits" in prompt
