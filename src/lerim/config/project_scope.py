"""Project matching helpers for Lerim session and repo identity logic."""

from __future__ import annotations

from pathlib import Path


def git_root_for(path: Path | None = None) -> Path | None:
    """Return the nearest directory that contains ``.git`` starting from ``path``."""
    start = (path or Path.cwd()).resolve()
    current = start
    while True:
        if (current / ".git").exists():
            return current
        if current.parent == current:
            return None
        current = current.parent


def match_session_project(
    session_cwd: str | None,
    projects: dict[str, str],
) -> tuple[str, Path] | None:
    """Match a session's cwd against registered projects by path prefix.

    Returns (project_name, project_path) for the most specific (longest path)
    match, or None if no registered project contains this cwd.
    """
    if not session_cwd:
        return None
    cwd_resolved = Path(session_cwd).resolve()
    best: tuple[str, Path] | None = None
    best_depth = -1
    for name, project_path_str in projects.items():
        project_resolved = Path(project_path_str).expanduser().resolve()
        if cwd_resolved == project_resolved or project_resolved in cwd_resolved.parents:
            depth = len(project_resolved.parts)
            if depth > best_depth:
                best = (name, project_resolved)
                best_depth = depth
    return best


if __name__ == "__main__":
    """Run a real-path smoke test for scope resolution logic."""
    cwd = Path.cwd()
    root = git_root_for(cwd)
    if root is not None:
        assert root.exists()
