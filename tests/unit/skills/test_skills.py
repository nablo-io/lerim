"""Tests for bundled skill files in src/lerim/skills/."""

from __future__ import annotations

import unittest
from pathlib import Path

import yaml


SKILLS_DIR = Path(__file__).parent.parent.parent.parent / "src" / "lerim" / "skills"

EXPECTED_SKILL_FILES = [
    "SKILL.md",
    "cli-reference.md",
]


class TestSkills(unittest.TestCase):
    def test_only_supported_skill_directories_exist(self) -> None:
        """Skills dir should not have unexpected subdirectories."""
        actual = sorted(
            path.name
            for path in SKILLS_DIR.iterdir()
            if path.is_dir() and path.name != "__pycache__"
        )
        self.assertEqual(actual, [])

    def test_skill_files_exist(self) -> None:
        for filename in EXPECTED_SKILL_FILES:
            path = SKILLS_DIR / filename
            self.assertTrue(path.exists(), f"Missing skill file: {path}")

    def test_skill_has_valid_frontmatter(self) -> None:
        path = SKILLS_DIR / "SKILL.md"
        text = path.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("---\n"), "No frontmatter in SKILL.md")
        end = text.index("---\n", 4)
        fm_text = text[4:end]
        fm = yaml.safe_load(fm_text)
        self.assertIsInstance(fm, dict, "Invalid frontmatter in SKILL.md")
        self.assertIn("name", fm, "Missing 'name' in SKILL.md")
        self.assertIn("description", fm, "Missing 'description' in SKILL.md")

    def test_skill_has_body_content(self) -> None:
        path = SKILLS_DIR / "SKILL.md"
        text = path.read_text(encoding="utf-8")
        end = text.index("---\n", 4) + 4
        body = text[end:].strip()
        self.assertTrue(len(body) > 20, "SKILL.md has no body content")
