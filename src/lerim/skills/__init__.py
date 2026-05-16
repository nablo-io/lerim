"""Bundled skill files for supported agent integration.

Provides SKILL.md and cli-reference.md that can be installed into agent
skill directories via ``lerim skill install``.
"""

from __future__ import annotations

from pathlib import Path

SKILLS_DIR = Path(__file__).parent
"""Absolute path to the directory containing bundled skill files."""
