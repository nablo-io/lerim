"""Host-only generic trace import orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lerim.config.settings import Config, get_config
from lerim.context import ScopeIdentity, resolve_scope_identity
from lerim.server.runtime import LerimRuntime
from lerim.traces.envelope import load_generic_trace, write_compact_trace


@dataclass(frozen=True)
class TraceImportResult:
    """Result returned after importing and ingesting a generic trace."""

    trace_id: str
    normalized_trace_path: Path
    scope_identity: ScopeIdentity
    session_id: str
    ingest_result: dict[str, Any]


def import_trace_file(
    *,
    trace_path: Path,
    source_name: str,
    source_profile: str,
    scope_type: str,
    scope: str,
    scope_label: str | None = None,
    session_id: str | None = None,
    config: Config | None = None,
) -> TraceImportResult:
    """Normalize, register, and extract one explicit generic trace file."""
    cfg = config or get_config()
    resolved_trace = trace_path.expanduser().resolve()
    normalized = load_generic_trace(resolved_trace)
    scope_identity = resolve_scope_identity(
        scope_type=scope_type,
        scope=scope,
        scope_label=scope_label,
    )
    normalized_path = _normalized_trace_path(
        cfg,
        scope_identity=scope_identity,
        trace_id=normalized.trace_id,
    )
    write_compact_trace(normalized, normalized_path)
    resolved_session_id = session_id or normalized.trace_id
    runtime = LerimRuntime(config=cfg)
    ingest_result = runtime.ingest_imported_trace(
        normalized_path,
        scope_identity=scope_identity,
        session_id=resolved_session_id,
        agent_type=source_name or "generic",
        source_name=source_name,
        source_profile=source_profile,
        session_meta={
            "started_at": normalized.started_at or "",
            "source_trace_path": str(resolved_trace),
            "message_count": normalized.message_count,
        },
    )
    return TraceImportResult(
        trace_id=normalized.trace_id,
        normalized_trace_path=normalized_path,
        scope_identity=scope_identity,
        session_id=resolved_session_id,
        ingest_result=ingest_result,
    )


def _normalized_trace_path(
    config: Config,
    *,
    scope_identity: ScopeIdentity,
    trace_id: str,
) -> Path:
    """Return the canonical workspace path for normalized imports."""
    return (
        config.global_data_dir
        / "workspace"
        / "imports"
        / scope_identity.scope_type
        / scope_identity.scope_id
        / f"{trace_id}.jsonl"
    )


if __name__ == "__main__":
    """Run a tiny path construction smoke check."""
    from tempfile import TemporaryDirectory

    from lerim.config.settings import Config

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        cfg = Config(global_data_dir=root, server_port=3030)
        scope = resolve_scope_identity(scope_type="domain", scope="support")
        path = _normalized_trace_path(cfg, scope_identity=scope, trace_id="trace_demo")
        assert "imports" in path.parts
