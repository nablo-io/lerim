"""Tests for project identity helpers."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from lerim.context.project_identity import (
    ProjectIdentity,
    _slugify,
    resolve_project_identity,
)


class TestSlugify:
    """Tests for _slugify helper."""

    def test_simple_name(self):
        assert _slugify("MyProject") == "myproject"

    def test_spaces_replaced(self):
        assert _slugify("my cool project") == "my-cool-project"

    def test_special_chars_removed(self):
        assert _slugify("proj@#$%^&.com") == "proj-com"

    def test_leading_trailing_dashes_stripped(self):
        assert _slugify("---hello---") == "hello"

    def test_empty_returns_default(self):
        assert _slugify("") == "project"

    def test_none_returns_default(self):
        assert _slugify(None) == "project"

    def test_only_special_chars_returns_default(self):
        assert _slugify("@#$%^") == "project"

    def test_mixed_case_lowered(self):
        assert _slugify("CamelCaseName") == "camelcasename"

    def test_multiple_dashes_collapsed(self):
        result = _slugify("a---b")
        assert "---" not in result
        assert result == "a-b"


class TestProjectIdentityFrozen:
    """Tests for ProjectIdentity frozen dataclass."""

    def test_frozen_raises_on_set(self, tmp_path):
        identity = ProjectIdentity(
            project_id="proj_abc",
            project_slug="test",
            repo_path=tmp_path,
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            identity.project_id = "proj_xyz"

    def test_frozen_raises_on_slug_set(self, tmp_path):
        identity = ProjectIdentity(
            project_id="proj_abc",
            project_slug="test",
            repo_path=tmp_path,
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            identity.project_slug = "other"

    def test_fields_accessible(self, tmp_path):
        identity = ProjectIdentity(
            project_id="proj_abc",
            project_slug="test",
            repo_path=tmp_path,
        )
        assert identity.project_id == "proj_abc"
        assert identity.project_slug == "test"
        assert identity.repo_path == tmp_path


class TestResolveProjectIdentity:
    """Tests for resolve_project_identity."""

    def test_with_mock_git_root(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "lerim.config.project_scope.git_root_for",
            lambda _p=None: tmp_path,
        )
        identity = resolve_project_identity(tmp_path)
        assert identity.project_id.startswith("proj_")
        assert len(identity.project_id) == len("proj_") + 12

    def test_deterministic_same_path(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "lerim.config.project_scope.git_root_for",
            lambda _p=None: tmp_path,
        )
        id1 = resolve_project_identity(tmp_path)
        id2 = resolve_project_identity(tmp_path)
        assert id1.project_id == id2.project_id
        assert id1.project_slug == id2.project_slug

    def test_different_paths_different_ids(self, tmp_path, monkeypatch):
        dir_a = tmp_path / "project-a"
        dir_b = tmp_path / "project-b"
        dir_a.mkdir()
        dir_b.mkdir()

        monkeypatch.setattr(
            "lerim.config.project_scope.git_root_for",
            lambda p=None: p,
        )
        id_a = resolve_project_identity(dir_a)
        id_b = resolve_project_identity(dir_b)
        assert id_a.project_id != id_b.project_id

    def test_slug_from_directory_name(self, tmp_path, monkeypatch):
        project_dir = tmp_path / "My Awesome Repo"
        project_dir.mkdir()
        monkeypatch.setattr(
            "lerim.config.project_scope.git_root_for",
            lambda _p=None: project_dir,
        )
        identity = resolve_project_identity(project_dir)
        assert identity.project_slug == "my-awesome-repo"

    def test_explicit_scoped_paths_under_same_git_root_get_distinct_ids(self, tmp_path, monkeypatch):
        repo_root = tmp_path / "monorepo"
        project_a = repo_root / "apps" / "alpha"
        project_b = repo_root / "apps" / "beta"
        project_a.mkdir(parents=True)
        project_b.mkdir(parents=True)
        monkeypatch.setattr(
            "lerim.context.project_identity.git_root_for",
            lambda _p=None: repo_root,
        )

        identity_a = resolve_project_identity(project_a)
        identity_b = resolve_project_identity(project_b)

        assert identity_a.project_id != identity_b.project_id
        assert identity_a.repo_path == project_a.resolve()
        assert identity_b.repo_path == project_b.resolve()

    def test_no_git_root_uses_candidate(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "lerim.config.project_scope.git_root_for",
            lambda _p=None: None,
        )
        identity = resolve_project_identity(tmp_path)
        assert identity.repo_path == tmp_path.resolve()
