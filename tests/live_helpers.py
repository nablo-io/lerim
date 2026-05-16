"""Shared helpers for live smoke, integration, and end-to-end QA tests."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
import sqlite_vec

from lerim.config.settings import Config, get_config, reload_config
from lerim.context.spec import RECORD_KIND_SPECS

REQUIRED_CONTEXT_TABLES = {
    "projects",
    "records",
    "record_embeddings",
    "record_versions",
    "records_fts",
    "schema_meta",
    "scopes",
    "sessions",
}
IGNORED_FTS_SHADOW_TABLES = {
    "records_fts_config",
    "records_fts_content",
    "records_fts_data",
    "records_fts_docsize",
    "records_fts_idx",
}
IGNORED_VEC_SHADOW_PREFIXES = (
    "record_embeddings_",
)
FORBIDDEN_CONTEXT_TABLES = {
    "evidence",
    "record_links",
    "session_findings",
}
FRAMEWORK_TOOL_NAMES = {
    "final_result",
}
EXTRACT_EVENT_NAMES = frozenset(
    {
        "resolve_scope",
        "read_window",
        "scan_window",
        "filter_signals",
        "synthesize_records",
        "review_records",
        "save_context",
        "model_retry",
    }
)
CONTEXT_CURATOR_EVENT_NAMES = frozenset(
    {
        "load_inventory",
        "build_similarity_clusters",
        "review_cluster",
        "review_health_batch",
        "apply_context_curation_action",
        "model_retry",
        "final_result",
    }
)
_API_KEY_ATTRS = {
    "minimax": "minimax_api_key",
    "openai": "openai_api_key",
    "openrouter": "openrouter_api_key",
    "opencode_go": "opencode_api_key",
    "zai": "zai_api_key",
}


def _missing_required_field_count(
    conn: sqlite3.Connection,
    *,
    kind: str,
    required_fields: tuple[str, ...],
) -> int:
    """Count records missing one or more required typed fields for one kind."""
    if not required_fields:
        return 0
    missing_checks = " OR ".join(
        f"{field_name} IS NULL OR length(trim({field_name})) = 0"
        for field_name in required_fields
    )
    row = conn.execute(
        f"""
        SELECT COUNT(*)
        FROM records
        WHERE kind = ?
          AND ({missing_checks})
        """,
        (kind,),
    ).fetchone()
    return int(row[0]) if row is not None else 0


def require_live_agent_config() -> Config:
    """Return a live config or skip when the active provider cannot run."""
    config = reload_config()
    provider = config.agent_role.provider.strip().lower()
    api_key_attr = _API_KEY_ATTRS.get(provider)
    if api_key_attr and not getattr(config, api_key_attr):
        pytest.skip(f"live agent provider {provider!r} is selected but {api_key_attr} is missing")
    return config


def build_live_config(base: Path) -> Config:
    """Clone the current user config into an isolated temporary Lerim root."""
    source = require_live_agent_config()
    base.mkdir(parents=True, exist_ok=True)
    for relative in (
        Path("cache") / "traces",
        Path("index"),
        Path("logs"),
        Path("models") / "embeddings",
        Path("models") / "huggingface" / "hub",
        Path("observability") / "backups",
        Path("workspace") / "curate",
        Path("workspace") / "ingest",
    ):
        (base / relative).mkdir(parents=True, exist_ok=True)
    (base / "platforms.json").write_text("{}\n", encoding="utf-8")
    return replace(
        source,
        global_data_dir=base,
        sessions_db_path=base / "index" / "sessions.sqlite3",
        context_db_path=base / "context.sqlite3",
        platforms_path=base / "platforms.json",
        embedding_cache_dir=base / "models" / "embeddings",
        mlflow_enabled=False,
        agents={},
        projects={},
        cloud_token=None,
    )


def dump_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return BAML/LangGraph event history as stable JSON-like objects."""
    return [dict(message) for message in messages]


def extract_tool_names(payload: list[dict[str, Any]]) -> list[str]:
    """Extract event/function/action names from a serialized trace payload."""
    names: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            action = str(value.get("action") or "").strip()
            if action:
                names.append(action)
            function = str(value.get("function") or "").strip()
            if function:
                names.append(function)
            action_type = str(value.get("action_type") or "").strip()
            if action_type:
                names.append(action_type)
            for item in value.values():
                walk(item)
            return
        if isinstance(value, list):
            for item in value:
                walk(item)

    walk(payload)
    return names


def read_agent_trace_tool_names(agent_trace_path: Path) -> list[str]:
    """Read one on-disk agent trace and return its event/function/action names."""
    payload = json.loads(agent_trace_path.read_text(encoding="utf-8"))
    return extract_tool_names(payload)


def connect_context_db(db_path: Path) -> sqlite3.Connection:
    """Open one context DB connection with sqlite-vec loaded."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    return conn


def visible_context_tables(db_path: Path) -> set[str]:
    """Return canonical user-facing context DB tables, excluding FTS shadows."""
    with connect_context_db(db_path) as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
        ).fetchall()
    names = {str(row[0]) for row in rows}
    return {
        name
        for name in names
        if not name.startswith("sqlite_")
        and name not in IGNORED_FTS_SHADOW_TABLES
        and not any(name.startswith(prefix) for prefix in IGNORED_VEC_SHADOW_PREFIXES)
    }


def audit_context_db(db_path: Path) -> dict[str, Any]:
    """Collect DB quality metrics used by live QA tests."""
    user_tables = visible_context_tables(db_path)
    with connect_context_db(db_path) as conn:
        metrics = {
            "user_tables": user_tables,
            "record_count": int(conn.execute("SELECT COUNT(*) FROM records").fetchone()[0]),
            "version_count": int(conn.execute("SELECT COUNT(*) FROM record_versions").fetchone()[0]),
            "embedding_count": int(conn.execute("SELECT COUNT(*) FROM record_embeddings").fetchone()[0]),
            "embedding_models": sorted(
                str(row[0])
                for row in conn.execute(
                    "SELECT DISTINCT embedding_model FROM record_embeddings ORDER BY embedding_model"
                ).fetchall()
                if row[0]
            ),
            "fts_count": int(conn.execute("SELECT COUNT(*) FROM records_fts").fetchone()[0]),
            "blank_titles": int(
                conn.execute("SELECT COUNT(*) FROM records WHERE length(trim(title)) = 0").fetchone()[0]
            ),
            "blank_bodies": int(
                conn.execute("SELECT COUNT(*) FROM records WHERE length(trim(body)) = 0").fetchone()[0]
            ),
            "bad_decisions": _missing_required_field_count(
                conn,
                kind="decision",
                required_fields=RECORD_KIND_SPECS["decision"].required_fields,
            ),
            "bad_episodes": _missing_required_field_count(
                conn,
                kind="episode",
                required_fields=RECORD_KIND_SPECS["episode"].required_fields,
            ),
            "long_episode_bodies": int(
                conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM records
                    WHERE kind = 'episode'
                      AND length(trim(body)) > ?
                    """,
                    (RECORD_KIND_SPECS["episode"].body_max_chars,),
                ).fetchone()[0]
            ),
            "archived_without_valid_until": int(
                conn.execute(
                    "SELECT COUNT(*) FROM records WHERE status = 'archived' AND valid_until IS NULL"
                ).fetchone()[0]
            ),
        }
    return metrics


def assert_clean_context_schema(db_path: Path) -> None:
    """Assert that only the canonical context tables exist."""
    user_tables = visible_context_tables(db_path)
    assert user_tables == REQUIRED_CONTEXT_TABLES
    assert not (user_tables & FORBIDDEN_CONTEXT_TABLES)


def assert_quality_metrics(metrics: dict[str, Any]) -> None:
    """Assert key DB-quality invariants for release-style live tests."""
    assert metrics["blank_titles"] == 0
    assert metrics["blank_bodies"] == 0
    assert metrics["bad_decisions"] == 0
    assert metrics["bad_episodes"] == 0
    assert metrics["long_episode_bodies"] == 0
    assert metrics["archived_without_valid_until"] == 0
    assert metrics["record_count"] >= 1
    assert metrics["version_count"] >= metrics["record_count"]
    assert metrics["embedding_count"] == metrics["record_count"]
    assert metrics["embedding_models"] == [get_config().embedding_model_id]
    assert metrics["fts_count"] == metrics["record_count"]
