"""Unit tests for project matching helpers."""

from __future__ import annotations

from lerim.config.project_scope import git_root_for, match_session_project


def test_git_root_detection(tmp_path):
    """git_root_for finds the nearest .git ancestor."""
    (tmp_path / ".git").mkdir()
    sub = tmp_path / "a" / "b"
    sub.mkdir(parents=True)
    assert git_root_for(sub) == tmp_path


def test_git_root_none_outside_repo(tmp_path):
    """git_root_for returns None when no repo root exists."""
    isolated = tmp_path / "no-repo"
    isolated.mkdir()
    assert git_root_for(isolated) is None


def test_match_session_project_prefers_most_specific_parent(tmp_path):
    """match_session_project picks the deepest matching project path."""
    projects = {
        "root": str(tmp_path / "root"),
        "nested": str(tmp_path / "root" / "nested"),
    }
    session_cwd = str(tmp_path / "root" / "nested" / "service")
    matched = match_session_project(session_cwd, projects)
    assert matched is not None
    name, path = matched
    assert name == "nested"
    assert path == (tmp_path / "root" / "nested").resolve()
