"""Public context-store API for Lerim's simplified DB-only architecture."""

from lerim.context.embedding import (
    EMBEDDING_DIMS,
    EMBEDDING_MODEL_NAME,
    EmbeddingProvider,
    clear_embedding_provider_cache,
    get_embedding_provider,
)
from lerim.context.project_identity import ProjectIdentity, resolve_project_identity
from lerim.context.store import ALLOWED_KINDS, ALLOWED_STATUSES, ContextStore, SearchHit

__all__ = [
    "ALLOWED_KINDS",
    "ALLOWED_STATUSES",
    "ContextStore",
    "EMBEDDING_DIMS",
    "EMBEDDING_MODEL_NAME",
    "EmbeddingProvider",
    "ProjectIdentity",
    "SearchHit",
    "clear_embedding_provider_cache",
    "get_embedding_provider",
    "resolve_project_identity",
]
