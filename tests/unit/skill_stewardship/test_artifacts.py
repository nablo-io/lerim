"""Tests for instruction artifact scanning."""

from __future__ import annotations

from pathlib import Path

import pytest

from lerim.skill_stewardship.artifacts import scan_instruction_artifact


def test_scans_codex_skill_manifest(tmp_path: Path) -> None:
    """Codex-style skills expose SKILL.md plus standard support files."""
    skill = tmp_path / ".agents" / "skills" / "clean-code"
    (skill / "references").mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: clean-code\ndescription: Keep code simple.\n---\n\nUse small functions.\n",
        encoding="utf-8",
    )
    (skill / "references" / "simplification.md").write_text("Avoid pass-through wrappers.\n", encoding="utf-8")

    base, manifest, files = scan_instruction_artifact(skill)

    assert base == skill
    assert manifest.target_type == "codex_skill"
    assert manifest.entry_file == "SKILL.md"
    assert "name" in manifest.required_frontmatter
    assert {item.relative_path for item in files} == {"SKILL.md", "references/simplification.md"}


def test_scans_plain_agents_file(tmp_path: Path) -> None:
    """A standalone AGENTS.md is treated as a plain instruction target."""
    path = tmp_path / "AGENTS.md"
    path.write_text("# Project Instructions\n\nRun tests before finishing.\n", encoding="utf-8")

    base, manifest, files = scan_instruction_artifact(path)

    assert base == tmp_path
    assert manifest.target_type == "agents_md"
    assert manifest.entry_file == "AGENTS.md"
    assert files[0].relative_path == "AGENTS.md"


def test_scans_claude_context_file(tmp_path: Path) -> None:
    """A standalone CLAUDE.md is classified as Claude context, not OpenCode."""
    path = tmp_path / "CLAUDE.md"
    path.write_text("# Claude Instructions\n\nUse the project tests.\n", encoding="utf-8")

    _base, manifest, files = scan_instruction_artifact(path)

    assert manifest.target_type == "claude_context"
    assert manifest.entry_file == "CLAUDE.md"
    assert files[0].relative_path == "CLAUDE.md"


def test_scan_skips_support_symlink_outside_target(tmp_path: Path) -> None:
    """Support files symlinked outside the artifact root do not crash scans."""
    skill = tmp_path / "skill"
    references = skill / "references"
    outside = tmp_path / "outside.md"
    references.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: demo\ndescription: Demo.\n---\n\nUse small updates.\n",
        encoding="utf-8",
    )
    outside.write_text("Outside target.\n", encoding="utf-8")
    try:
        (references / "outside.md").symlink_to(outside)
    except OSError:
        pytest.skip("filesystem does not allow symlink creation")

    _base, _manifest, files = scan_instruction_artifact(skill)

    assert {item.relative_path for item in files} == {"SKILL.md"}


def test_scan_prunes_large_irrelevant_directories(tmp_path: Path) -> None:
    """Directory targets do not walk dependency/build trees before applying caps."""
    skill = tmp_path / "skill"
    references = skill / "references"
    ignored = references / "node_modules" / "package"
    ignored.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: demo\ndescription: Demo.\n---\n\nUse small updates.\n",
        encoding="utf-8",
    )
    (references / "guide.md").write_text("Useful guidance.\n", encoding="utf-8")
    (ignored / "ignored.md").write_text("Should not be scanned.\n", encoding="utf-8")

    _base, _manifest, files = scan_instruction_artifact(skill)

    assert {item.relative_path for item in files} == {"SKILL.md", "references/guide.md"}
