"""Public context-store API for Lerim's simplified DB-only architecture."""

from __future__ import annotations

from importlib import import_module
from typing import Any

from lerim.context.project_identity import ProjectIdentity, resolve_project_identity
from lerim.context.scope_identity import (
    ALLOWED_SCOPE_TYPES,
    ScopeIdentity,
    resolve_scope_identity,
    scope_from_project,
)
from lerim.context.roles import (
    ALLOWED_RECORD_ROLES,
    DEFAULT_RECORD_ROLE,
    MAX_ROLE_PAYLOAD_CHARS,
    ROLE_PAYLOAD_KEYS,
    RecordRole,
    normalize_record_role,
    normalize_role_payload,
    role_payload_search_text,
)
from lerim.context.spec import (
    ALLOWED_CHANGE_KINDS,
    ALLOWED_KINDS,
    ALLOWED_ROLES,
    ALLOWED_STATUSES,
    DURABLE_RECORD_KINDS,
    MAX_DURABLE_BODY_CHARS,
    MAX_EPISODE_BODY_CHARS,
    MAX_EPISODE_OUTCOMES_CHARS,
    MAX_EPISODE_USER_INTENT_CHARS,
    MAX_EPISODE_WHAT_HAPPENED_CHARS,
    MAX_RECORD_TITLE_CHARS,
    RECORD_KIND_SPECS,
    RecordChangeKind,
    RecordKind,
    RecordStatus,
    format_durable_record_kinds,
    normalize_record_kind,
    normalize_record_payload,
    normalize_record_status,
    record_search_text,
    record_validation_message,
)

_LAZY_EXPORTS = {
    "ContextStore": ("lerim.context.store", "ContextStore"),
    "EMBEDDING_DIMS": ("lerim.context.embedding", "EMBEDDING_DIMS"),
    "EMBEDDING_MODEL_NAME": ("lerim.context.embedding", "EMBEDDING_MODEL_NAME"),
    "EmbeddingProvider": ("lerim.context.embedding", "EmbeddingProvider"),
    "clear_embedding_provider_cache": (
        "lerim.context.embedding",
        "clear_embedding_provider_cache",
    ),
    "get_embedding_provider": ("lerim.context.embedding", "get_embedding_provider"),
}


def __getattr__(name: str) -> Any:
    """Lazily load heavy context exports only when callers request them."""
    if name not in _LAZY_EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr_name = _LAZY_EXPORTS[name]
    value = getattr(import_module(module_name), attr_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """Return public names, including lazy context exports."""
    return sorted(set(globals()) | set(_LAZY_EXPORTS))

__all__ = [
    "ALLOWED_CHANGE_KINDS",
    "ALLOWED_KINDS",
    "ALLOWED_RECORD_ROLES",
    "ALLOWED_ROLES",
    "ALLOWED_STATUSES",
    "ALLOWED_SCOPE_TYPES",
    "ContextStore",
    "DEFAULT_RECORD_ROLE",
    "DURABLE_RECORD_KINDS",
    "EMBEDDING_DIMS",
    "EMBEDDING_MODEL_NAME",
    "EmbeddingProvider",
    "MAX_DURABLE_BODY_CHARS",
    "MAX_EPISODE_BODY_CHARS",
    "MAX_EPISODE_OUTCOMES_CHARS",
    "MAX_EPISODE_USER_INTENT_CHARS",
    "MAX_EPISODE_WHAT_HAPPENED_CHARS",
    "MAX_RECORD_TITLE_CHARS",
    "MAX_ROLE_PAYLOAD_CHARS",
    "ProjectIdentity",
    "ROLE_PAYLOAD_KEYS",
    "RECORD_KIND_SPECS",
    "RecordChangeKind",
    "RecordKind",
    "RecordRole",
    "RecordStatus",
    "ScopeIdentity",
    "clear_embedding_provider_cache",
    "format_durable_record_kinds",
    "get_embedding_provider",
    "normalize_record_kind",
    "normalize_record_payload",
    "normalize_record_role",
    "normalize_record_status",
    "normalize_role_payload",
    "record_search_text",
    "record_validation_message",
    "resolve_project_identity",
    "resolve_scope_identity",
    "role_payload_search_text",
    "scope_from_project",
]
