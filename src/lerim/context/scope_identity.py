"""Scope identity helpers for project and generic trace context."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from lerim.context.project_identity import ProjectIdentity, resolve_project_identity

ALLOWED_SCOPE_TYPES = ("project", "domain", "user", "session", "workspace", "custom")


def _slugify(value: str, *, fallback: str = "scope") -> str:
    """Convert arbitrary scope labels into a stable lowercase slug."""
    text = re.sub(r"[^a-zA-Z0-9]+", "-", (value or "").strip().lower()).strip("-")
    return text or fallback


def _clean_scope_type(value: str) -> str:
    """Normalize and validate a public scope type."""
    scope_type = str(value or "").strip().lower().replace("_", "-")
    if scope_type not in ALLOWED_SCOPE_TYPES:
        allowed = ",".join(ALLOWED_SCOPE_TYPES)
        raise ValueError(f"invalid_scope_type:{scope_type or '<empty>'}:allowed={allowed}")
    return scope_type


@dataclass(frozen=True)
class ScopeIdentity:
    """Stable scope identity stored with sessions and records."""

    scope_type: str
    scope_id: str
    scope_slug: str
    label: str
    repo_path: Path | None = None

    @property
    def scope_key(self) -> str:
        """Return the compact type/id key used in logs and manifests."""
        return f"{self.scope_type}:{self.scope_id}"


def scope_from_project(identity: ProjectIdentity) -> ScopeIdentity:
    """Build a project scope from an existing project identity."""
    return ScopeIdentity(
        scope_type="project",
        scope_id=identity.project_id,
        scope_slug=identity.project_slug,
        label=identity.project_slug,
        repo_path=identity.repo_path,
    )


def resolve_scope_identity(
    *,
    scope_type: str,
    scope: str,
    scope_label: str | None = None,
) -> ScopeIdentity:
    """Resolve CLI/API scope input into a deterministic scope identity."""
    normalized_type = _clean_scope_type(scope_type)
    raw_scope = str(scope or "").strip()
    if not raw_scope:
        raise ValueError("scope_required")
    if normalized_type == "project":
        project = resolve_project_identity(Path(raw_scope).expanduser().resolve())
        return scope_from_project(project)

    label = str(scope_label or raw_scope).strip() or raw_scope
    digest_source = f"{normalized_type}:{raw_scope}"
    digest = hashlib.sha1(digest_source.encode("utf-8")).hexdigest()[:12]
    slug = _slugify(label, fallback=normalized_type)
    return ScopeIdentity(
        scope_type=normalized_type,
        scope_id=f"scope_{digest}",
        scope_slug=slug,
        label=label,
        repo_path=None,
    )


if __name__ == "__main__":
    """Run a small deterministic identity smoke check."""
    scope = resolve_scope_identity(scope_type="domain", scope="support")
    assert scope.scope_key.startswith("domain:scope_")
    assert scope.scope_slug == "support"
    print("scope identity: self-test passed")
