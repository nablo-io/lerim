"""Project identity helpers for the global Lerim context database.

This module turns a repo path into a stable project identity so the global
`~/.lerim/context.sqlite3` store can separate context by project without
creating per-project databases.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from lerim.config.project_scope import git_root_for


def _slugify(value: str) -> str:
    """Convert arbitrary project names into a stable lowercase slug."""
    text = re.sub(r"[^a-zA-Z0-9]+", "-", (value or "").strip().lower()).strip("-")
    return text or "project"


@dataclass(frozen=True)
class ProjectIdentity:
    """Stable project identity stored inside the global context DB."""

    project_id: str
    project_slug: str
    repo_path: Path


def resolve_project_identity(repo_path: Path | None = None) -> ProjectIdentity:
    """Resolve the current repo path into a deterministic project identity."""
    candidate = (repo_path or Path.cwd()).expanduser().resolve()
    project_root = candidate if repo_path is not None else (git_root_for(candidate) or candidate)
    repo_path_str = str(candidate if repo_path is not None else project_root)
    digest = hashlib.sha1(repo_path_str.encode("utf-8")).hexdigest()[:12]
    slug = _slugify((candidate if repo_path is not None else project_root).name)
    return ProjectIdentity(
        project_id=f"proj_{digest}",
        project_slug=slug,
        repo_path=candidate if repo_path is not None else project_root,
    )


if __name__ == "__main__":
    """Run a small determinism smoke check."""
    identity = resolve_project_identity(Path.cwd())
    assert identity.project_id.startswith("proj_")
    assert identity.project_slug
    assert identity.repo_path.exists()
    print("project identity: self-test passed")
