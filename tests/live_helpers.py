"""Shared helpers for live smoke, integration, and end-to-end QA tests."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
import sqlite_vec
from pydantic_ai.messages import ModelMessage, ModelMessagesTypeAdapter

from lerim.config.settings import Config, get_config, reload_config
from lerim.context.spec import RECORD_KIND_SPECS

REQUIRED_CONTEXT_TABLES = {
    "projects",
    "records",
    "record_embeddings",
    "record_versions",
    "records_fts",
    "schema_meta",
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
REMOVED_TOOL_NAMES = {
    "archive",
    "archive_record",
    "context_query",
    "create_record",
    "edit",
    "fetch_records",
    "grep",
    "list_records",
    "note",
    "prune",
    "read",
    "scan",
    "search_records",
    "supersede_record",
    "trace_read",
    "update_record",
    "verify_index",
    "write",
}
FRAMEWORK_TOOL_NAMES = {
    "final_result",
}
EXTRACT_TOOL_NAMES = {
    "save_context",
    "get_context",
    "note_trace_findings",
    "prune_trace_reads",
    "search_context",
    "read_trace",
    "revise_context",
}
MAINTAIN_TOOL_NAMES = {
    "archive_context",
    "get_context",
    "list_context",
    "search_context",
    "supersede_context",
    "revise_context",
}
ASK_TOOL_NAMES = {
    "count_context",
    "get_context",
    "list_context",
    "search_context",
}
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
        Path("cache"),
        Path("index"),
        Path("logs"),
        Path("observability") / "backups",
        Path("workspace") / "maintain",
        Path("workspace") / "sync",
    ):
        (base / relative).mkdir(parents=True, exist_ok=True)
    (base / "platforms.json").write_text("{}\n", encoding="utf-8")
    return replace(
        source,
        global_data_dir=base,
        sessions_db_path=base / "index" / "sessions.sqlite3",
        context_db_path=base / "context.sqlite3",
        platforms_path=base / "platforms.json",
        mlflow_enabled=False,
        agents={},
        projects={},
        cloud_token=None,
    )


def dump_messages(messages: list[ModelMessage]) -> list[dict[str, Any]]:
    """Serialize PydanticAI message history into stable JSON-like objects."""
    return ModelMessagesTypeAdapter.dump_python(messages, mode="json")


def extract_tool_names(payload: list[dict[str, Any]]) -> list[str]:
    """Extract tool-call names from a serialized message payload."""
    names: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            if value.get("part_kind") == "tool-call":
                tool_name = str(value.get("tool_name") or "").strip()
                if tool_name:
                    names.append(tool_name)
            for item in value.values():
                walk(item)
            return
        if isinstance(value, list):
            for item in value:
                walk(item)

    walk(payload)
    return names


def read_agent_trace_tool_names(agent_trace_path: Path) -> list[str]:
    """Read one on-disk agent trace and return its tool-call names."""
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


def assert_no_removed_tools(tool_names: list[str]) -> None:
    """Assert that one agent trace avoids removed tool names."""
    assert not (set(tool_names) & REMOVED_TOOL_NAMES)
