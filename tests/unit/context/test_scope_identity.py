"""Tests for scope identity helpers."""

from __future__ import annotations

import dataclasses

import pytest

from lerim.context.project_identity import ProjectIdentity
from lerim.context.scope_identity import (
    ALLOWED_SCOPE_TYPES,
    ScopeIdentity,
    resolve_scope_identity,
    scope_from_project,
)


def test_allowed_scope_types_include_project_and_generic_kinds():
    """Supported scope types cover compatibility and generic imports."""
    assert ALLOWED_SCOPE_TYPES == (
        "project",
        "domain",
        "user",
        "session",
        "workspace",
        "custom",
    )


def test_scope_identity_is_frozen():
    """ScopeIdentity is immutable once resolved."""
    scope = ScopeIdentity(
        scope_type="domain",
        scope_id="scope_abc",
        scope_slug="support",
        label="support",
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        scope.scope_id = "scope_other"


def test_scope_key_combines_type_and_id():
    """scope_key returns the stable compact lookup key."""
    scope = ScopeIdentity(
        scope_type="domain",
        scope_id="scope_abc",
        scope_slug="support",
        label="support",
    )
    assert scope.scope_key == "domain:scope_abc"


def test_scope_from_project_preserves_project_identity(tmp_path):
    """Project scopes use the project id and slug directly."""
    project = ProjectIdentity(
        project_id="proj_abc",
        project_slug="demo",
        repo_path=tmp_path,
    )

    scope = scope_from_project(project)

    assert scope.scope_type == "project"
    assert scope.scope_id == "proj_abc"
    assert scope.scope_slug == "demo"
    assert scope.repo_path == tmp_path


def test_resolve_scope_identity_is_deterministic_for_generic_scope():
    """Generic scopes derive deterministic ids from type and scope token."""
    first = resolve_scope_identity(scope_type="domain", scope="support")
    second = resolve_scope_identity(scope_type="domain", scope="support")

    assert first == second
    assert first.scope_id.startswith("scope_")
    assert first.scope_slug == "support"
    assert first.label == "support"


def test_resolve_scope_identity_uses_explicit_label():
    """Explicit labels shape display text and slug without changing id input."""
    scope = resolve_scope_identity(
        scope_type="custom",
        scope="customer-support-v1",
        scope_label="Customer Support",
    )

    assert scope.label == "Customer Support"
    assert scope.scope_slug == "customer-support"


def test_resolve_scope_identity_rejects_unknown_type():
    """Unknown scope types fail at the identity boundary."""
    with pytest.raises(ValueError, match="invalid_scope_type"):
        resolve_scope_identity(scope_type="team", scope="support")


def test_resolve_scope_identity_requires_scope_token():
    """Empty generic scope tokens are invalid."""
    with pytest.raises(ValueError, match="scope_required"):
        resolve_scope_identity(scope_type="domain", scope="")
