"""Runtime orchestrator for Lerim ingest and context agent flows.

All agent-facing flows use BAML-backed runtime clients over the DB-only context
store.
"""

from __future__ import annotations

import json
import logging
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lerim.agents.context_answerer import run_context_answerer
from lerim.agents.contracts import (
    ContextCuratorResultContract,
    IngestResultContract,
    ContextBriefResultContract,
)
from lerim.agents.trace_ingestion import TraceIngestionResult, TraceIngestionRunDetails, run_trace_ingestion
from lerim.agents.context_curator import run_context_curator
from lerim.agents.mlflow_observability import finish_mlflow_run, lerim_mlflow_run
from lerim.agents.context_brief import compile_context_brief
from lerim.config.settings import Config, get_config
from lerim.context import ProjectIdentity, ScopeIdentity, resolve_project_identity, scope_from_project
from lerim.context_brief import (
    CONTEXT_BRIEF_FILENAME,
    CONTEXT_BRIEF_OPERATION,
    build_manifest,
    count_changed_records_since,
    empty_context_brief_draft,
    included_record_ids,
    load_candidate_records,
    render_context_brief_markdown,
    utc_now_iso,
    validate_draft,
    ContextBriefProject,
    context_brief_paths,
)

logger = logging.getLogger("lerim.runtime")


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def _new_run_folder(workspace_root: Path, operation: str) -> tuple[str, Path]:
    """Create one date-partitioned run folder and return its public run id."""
    now = datetime.now(timezone.utc)
    run_id = f"{operation}-{now.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}"
    run_folder = (
        workspace_root
        / now.strftime("%Y")
        / now.strftime("%m")
        / now.strftime("%d")
        / operation
        / run_id
    )
    run_folder.mkdir(parents=True, exist_ok=True)
    return run_id, run_folder


def _build_artifact_paths(run_folder: Path, *, include_session_log: bool) -> dict[str, Path]:
    """Return canonical workspace artifact paths for a run folder."""
    paths = {
        "agent_log": run_folder / "agent.log",
        "subagents_log": run_folder / "subagents.log",
        "manifest": run_folder / "manifest.json",
        "events": run_folder / "events.jsonl",
        "error": run_folder / "error.json",
        "agent_trace": run_folder / "agent_trace.json",
    }
    if include_session_log:
        paths["session_log"] = run_folder / "session.log"
    return paths


def _resolve_runtime_roots(
    *,
    config: Config,
) -> Path:
    """Return the canonical global workspace root.

    Run artifacts are always written under ``~/.lerim/workspace``.
    The DB-only architecture no longer allows callers to redirect
    artifacts into repo-local ``.lerim`` trees or any other custom path.
    """
    return config.global_data_dir / "workspace"


def _store_for_config(config: Config):
    """Return the canonical context store for the current config."""
    from lerim.context import ContextStore

    store = ContextStore(config.context_db_path)
    store.initialize()
    return store


def _record_change_counts(config: Config, session_id: str) -> dict[str, int]:
    """Count record version mutations written by one session-scoped agent run."""
    store = _store_for_config(config)
    with store.connect() as conn:
        rows = conn.execute(
            """
            SELECT change_kind, COUNT(1) AS total
            FROM record_versions
            WHERE changed_by_session_id = ?
            GROUP BY change_kind
            """,
            (session_id,),
        ).fetchall()
    return {str(row["change_kind"]): int(row["total"]) for row in rows}


# ---------------------------------------------------------------------------
# Artifact I/O
# ---------------------------------------------------------------------------


def _write_json_artifact(path: Path, payload: dict[str, Any]) -> None:
    """Write artifact payload as UTF-8 JSON with trailing newline."""
    path.write_text(
        json.dumps(payload, ensure_ascii=True, indent=2) + "\n", encoding="utf-8"
    )


def _append_jsonl_artifact(path: Path, payload: dict[str, Any]) -> None:
    """Append one compact JSON event to a run-local JSONL artifact."""
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=True, default=str) + "\n")


def _write_text_with_newline(path: Path, content: str) -> None:
    """Write text artifact ensuring exactly one trailing newline."""
    text = content if content.endswith("\n") else f"{content}\n"
    path.write_text(text, encoding="utf-8")


def _write_agent_trace(path: Path, messages: list[Any]) -> None:
    """Serialize agent message or event history to a stable JSON artifact."""
    path.write_text(json.dumps(messages, ensure_ascii=True, indent=2), encoding="utf-8")


def _write_trace_ingestion_agent_trace(path: Path, details: TraceIngestionRunDetails) -> None:
    """Serialize BAML/LangGraph trace-ingestion events to a stable JSON artifact."""
    payload = [event.model_dump(mode="json") for event in details.events]
    path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")


def _write_graph_agent_trace(path: Path, details: Any) -> None:
    """Serialize BAML/LangGraph events to a stable JSON artifact."""
    payload = [event.model_dump(mode="json") for event in details.events]
    path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")


def _write_error_artifact(path: Path, exc: Exception) -> None:
    """Persist a compact, structured failure artifact for a run."""
    _write_json_artifact(
        path,
        {
            "type": type(exc).__name__,
            "message": str(exc),
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        },
    )


def _mark_run_failed(
    *,
    artifact_paths: dict[str, Path],
    manifest: dict[str, Any],
    exc: Exception,
) -> None:
    """Record a failed run in manifest, events, trace placeholder, and error artifact."""
    finished_at = datetime.now(timezone.utc).isoformat()
    run_id = str(manifest.get("run_id") or "")
    manifest["status"] = "failed"
    manifest["completed_at"] = finished_at
    manifest["error"] = {"type": type(exc).__name__, "message": str(exc)}
    _write_json_artifact(artifact_paths["manifest"], manifest)
    _write_error_artifact(artifact_paths["error"], exc)
    _append_jsonl_artifact(
        artifact_paths["events"],
        {
            "ts": finished_at,
            "event": "failed",
            "run_id": run_id,
            "error_type": type(exc).__name__,
            "error": str(exc),
        },
    )
    if not artifact_paths["agent_trace"].exists():
        artifact_paths["agent_trace"].write_text("[]", encoding="utf-8")


def _build_answer_debug(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a sanitized context-answerer debug payload from BAML/retrieval events."""
    retrieval_actions = [
        event for event in events if str(event.get("kind") or "") == "retrieval"
    ]
    messages = [
        {
            "message_index": index,
            "kind": str(event.get("kind") or "event"),
            "parts": [
                {
                    "part_kind": str(
                        event.get("function")
                        or event.get("action_type")
                        or event.get("kind")
                        or "event"
                    ),
                    "content": event,
                }
            ],
        }
        for index, event in enumerate(events)
    ]
    return {
        "events": events,
        "retrieval_actions": retrieval_actions,
        "message_count": len(events),
        "messages": messages,
    }


class LerimRuntime:
    """Runtime orchestrator for ingest and context agent flows."""

    def __init__(
        self,
        default_cwd: str | None = None,
        config: Config | None = None,
    ) -> None:
        """Create runtime with validated provider configuration."""
        cfg = config or get_config()
        self.config = cfg
        self._default_cwd = default_cwd

        from lerim.config.providers import validate_provider_for_role

        validate_provider_for_role(cfg.agent_role.provider, "agent")

    @staticmethod
    def generate_session_id() -> str:
        """Generate a unique session ID for interactive context answering."""
        return f"lerim-{secrets.token_hex(6)}"

    # ------------------------------------------------------------------
    # Trace-ingestion flow
    # ------------------------------------------------------------------

    def ingest(
        self,
        trace_path: str | Path,
        session_id: str | None = None,
        agent_type: str = "unknown",
        session_meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Run record-write trace ingestion and return stable contract payload."""
        trace_file = Path(trace_path).expanduser().resolve()
        if not trace_file.exists() or not trace_file.is_file():
            raise FileNotFoundError(f"trace_path_missing:{trace_file}")

        repo_root = Path(self._default_cwd or Path.cwd()).expanduser().resolve()
        return self._ingest_inner(
            trace_file,
            repo_root=repo_root,
            session_id=session_id,
            agent_type=agent_type,
            session_meta=session_meta or {},
        )

    def ingest_imported_trace(
        self,
        trace_path: str | Path,
        *,
        scope_identity: ScopeIdentity,
        session_id: str | None = None,
        agent_type: str = "generic",
        source_name: str | None = None,
        source_profile: str | None = None,
        session_meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Ingest one user-imported trace into a non-project or project scope."""
        trace_file = Path(trace_path).expanduser().resolve()
        if not trace_file.exists() or not trace_file.is_file():
            raise FileNotFoundError(f"trace_path_missing:{trace_file}")
        repo_root = scope_identity.repo_path if scope_identity.scope_type == "project" else None
        return self._ingest_inner(
            trace_file,
            repo_root=repo_root,
            session_id=session_id,
            agent_type=agent_type,
            session_meta=session_meta or {},
            scope_identity=scope_identity,
            source_name=source_name,
            source_profile=source_profile,
        )

    def _ingest_inner(
        self,
        trace_file: Path,
        *,
        repo_root: Path | None,
        session_id: str | None,
        agent_type: str,
        session_meta: dict[str, Any],
        scope_identity: ScopeIdentity | None = None,
        source_name: str | None = None,
        source_profile: str | None = None,
    ) -> dict[str, Any]:
        """Inner trace-ingestion logic called by ingest()."""
        project_identity = resolve_project_identity(repo_root) if repo_root is not None else None
        resolved_scope = scope_identity or (
            scope_from_project(project_identity) if project_identity is not None else None
        )
        if resolved_scope is None:
            raise ValueError("scope_identity_required")
        resolved_workspace_root = _resolve_runtime_roots(config=self.config)
        store = _store_for_config(self.config)
        if project_identity is not None:
            store.register_project(project_identity)
        else:
            store.register_scope(
                resolved_scope,
                source_name=source_name or agent_type,
                source_profile=source_profile or agent_type,
            )
        resolved_session_id = session_id or trace_file.stem
        store.upsert_session(
            project_id=project_identity.project_id if project_identity else None,
            session_id=resolved_session_id,
            agent_type=agent_type,
            source_trace_ref=str(trace_file),
            repo_path=str(project_identity.repo_path) if project_identity else None,
            cwd=str(session_meta.get("cwd") or (project_identity.repo_path if project_identity else "")) or None,
            started_at=str(session_meta.get("started_at") or ""),
            model_name=str(self.config.agent_role.model),
            instructions_text=str(session_meta.get("instructions_text") or "")[:4000]
            or None,
            prompt_text=str(session_meta.get("prompt_text") or "")[:4000] or None,
            scope_identity=resolved_scope,
            source_name=source_name or agent_type,
            source_profile=source_profile or agent_type,
            metadata=session_meta,
        )

        run_id, run_folder = _new_run_folder(resolved_workspace_root, "ingest")
        artifact_paths = _build_artifact_paths(run_folder, include_session_log=True)
        metadata = {
            "run_id": run_id,
            "trace_path": str(trace_file),
            "repo_name": repo_root.name if repo_root is not None else "",
            "scope_type": resolved_scope.scope_type,
            "scope_id": resolved_scope.scope_id,
            "scope_label": resolved_scope.label,
        }
        _write_json_artifact(artifact_paths["session_log"], metadata)
        artifact_paths["subagents_log"].write_text("", encoding="utf-8")
        manifest = {
            "run_id": run_id,
            "operation": "ingest",
            "status": "running",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "mlflow_client_request_id": run_id,
            "session_id": resolved_session_id,
            "agent_type": agent_type,
            "trace_path": str(trace_file),
            "project_id": project_identity.project_id if project_identity else None,
            "project": project_identity.project_slug if project_identity else resolved_scope.scope_slug,
            "repo_path": str(project_identity.repo_path) if project_identity else None,
            "scope_type": resolved_scope.scope_type,
            "scope_id": resolved_scope.scope_id,
            "scope_label": resolved_scope.label,
            "workspace_root": str(resolved_workspace_root),
            "run_folder": str(run_folder),
            "artifacts": {key: str(path) for key, path in artifact_paths.items()},
        }
        _write_json_artifact(artifact_paths["manifest"], manifest)
        _append_jsonl_artifact(
            artifact_paths["events"],
            {"ts": manifest["started_at"], "event": "started", "run_id": run_id},
        )

        try:
            with lerim_mlflow_run(
                enabled=self.config.mlflow_enabled,
                operation="ingest",
                run_id=run_id,
                session_id=resolved_session_id,
                project_id=project_identity.project_id if project_identity else resolved_scope.scope_id,
                project_name=project_identity.project_slug if project_identity else resolved_scope.scope_slug,
                run_folder=run_folder,
                request_preview=f"ingest:{resolved_session_id}",
            ) as mlflow_run:
                result, details = self._run_trace_ingestion(
                    project_identity=project_identity,
                    scope_identity=resolved_scope,
                    session_id=resolved_session_id,
                    trace_path=trace_file,
                    session_started_at=str(session_meta.get("started_at") or ""),
                    source_name=source_name or agent_type,
                    source_profile=source_profile or agent_type,
                )

                response_text = (result.completion_summary or "").strip() or "(no response)"
                _write_text_with_newline(artifact_paths["agent_log"], response_text)

                try:
                    _write_trace_ingestion_agent_trace(artifact_paths["agent_trace"], details)
                except Exception as exc:
                    logger.warning(f"[ingest] Failed to write agent trace: {exc}")
                    artifact_paths["agent_trace"].write_text("[]", encoding="utf-8")

                counts = _record_change_counts(self.config, resolved_session_id)
                finished_at = datetime.now(timezone.utc).isoformat()
                records_updated = int(counts.get("update") or 0) + int(
                    counts.get("supersede") or 0
                )
                manifest["status"] = "succeeded"
                manifest["completed_at"] = finished_at
                manifest["records_created"] = int(counts.get("create") or 0)
                manifest["records_updated"] = records_updated
                manifest["records_archived"] = int(counts.get("archive") or 0)
                _write_json_artifact(artifact_paths["manifest"], manifest)
                _append_jsonl_artifact(
                    artifact_paths["events"],
                    {
                        "ts": finished_at,
                        "event": "succeeded",
                        "run_id": run_id,
                        "records_created": manifest["records_created"],
                        "records_updated": manifest["records_updated"],
                        "records_archived": manifest["records_archived"],
                    },
                )
                finish_mlflow_run(
                    mlflow_run,
                    final_status="succeeded",
                    response_preview=response_text,
                    outputs={
                        "completion_summary": response_text,
                        "llm_calls": details.llm_calls,
                        "records_created": manifest["records_created"],
                        "records_updated": manifest["records_updated"],
                        "records_archived": manifest["records_archived"],
                    },
                    records_created=manifest["records_created"],
                    records_updated=manifest["records_updated"],
                    records_archived=manifest["records_archived"],
                )

                payload = {
                    "trace_path": str(trace_file),
                    "context_db_path": str(self.config.context_db_path),
                    "project_id": project_identity.project_id if project_identity else None,
                    "scope_type": resolved_scope.scope_type,
                    "scope_id": resolved_scope.scope_id,
                    "scope_label": resolved_scope.label,
                    "workspace_root": str(resolved_workspace_root),
                    "run_folder": str(run_folder),
                    "artifacts": {key: str(path) for key, path in artifact_paths.items()},
                    "records_created": manifest["records_created"],
                    "records_updated": records_updated,
                    "records_archived": manifest["records_archived"],
                    "cost_usd": 0.0,
                }
                return IngestResultContract.model_validate(payload).model_dump(
                    mode="json"
                )
        except Exception as exc:
            _mark_run_failed(
                artifact_paths=artifact_paths,
                manifest=manifest,
                exc=exc,
            )
            raise

    def _run_trace_ingestion(
        self,
        *,
        project_identity: ProjectIdentity | None,
        scope_identity: ScopeIdentity,
        session_id: str,
        trace_path: Path,
        session_started_at: str,
        source_name: str | None = None,
        source_profile: str | None = None,
        max_attempts: int = 3,
    ) -> tuple[TraceIngestionResult, TraceIngestionRunDetails]:
        """Run trace ingestion with bounded retry before any session mutation is written."""
        for attempt in range(1, max_attempts + 1):
            try:
                return run_trace_ingestion(
                    context_db_path=self.config.context_db_path,
                    project_identity=project_identity,
                    scope_identity=scope_identity,
                    session_id=session_id,
                    trace_path=trace_path,
                    config=self.config,
                    session_started_at=session_started_at,
                    source_name=source_name,
                    source_profile=source_profile,
                    return_details=True,
                )
            except Exception:
                if attempt >= max_attempts:
                    raise
                counts = _record_change_counts(self.config, session_id)
                if any(int(value or 0) for value in counts.values()):
                    raise
                wait_time = min(2**attempt, 8)
                logger.warning(
                    f"[ingest] transient trace-ingestion failure before writes on attempt "
                    f"{attempt}/{max_attempts}; retrying in {wait_time}s..."
                )
                time.sleep(wait_time)

        raise RuntimeError("trace_ingestion_retry_exhausted")

    # ------------------------------------------------------------------
    # Context-curator flow
    # ------------------------------------------------------------------

    def curate(
        self,
        repo_root: str | Path | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Run context-curation flow and return stable contract payload."""
        resolved_repo_root = (
            Path(repo_root).expanduser().resolve()
            if repo_root
            else Path(self._default_cwd or Path.cwd()).expanduser().resolve()
        )
        return self._curate_inner(
            resolved_repo_root,
            session_id=session_id or self.generate_session_id(),
        )

    def _curate_inner(
        self,
        repo_root: Path,
        *,
        session_id: str,
    ) -> dict[str, Any]:
        """Inner context-curation logic called by curate()."""
        project_identity = resolve_project_identity(repo_root)
        resolved_workspace_root = _resolve_runtime_roots(config=self.config)
        store = _store_for_config(self.config)
        store.register_project(project_identity)
        store.upsert_session(
            project_id=project_identity.project_id,
            session_id=session_id,
            agent_type="context_curator",
            source_trace_ref=f"context_curator:{project_identity.project_id}",
            repo_path=str(project_identity.repo_path),
            cwd=str(project_identity.repo_path),
            started_at=datetime.now(timezone.utc).isoformat(),
            model_name=str(self.config.agent_role.model),
            instructions_text=None,
            prompt_text=None,
            metadata={},
        )

        run_id, run_folder = _new_run_folder(resolved_workspace_root, "curate")
        artifact_paths = _build_artifact_paths(run_folder, include_session_log=False)
        artifact_paths["subagents_log"].write_text("", encoding="utf-8")
        manifest = {
            "run_id": run_id,
            "operation": "curate",
            "status": "running",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "mlflow_client_request_id": run_id,
            "session_id": session_id,
            "agent_type": "context_curator",
            "project_id": project_identity.project_id,
            "project": project_identity.project_slug,
            "repo_path": str(project_identity.repo_path),
            "workspace_root": str(resolved_workspace_root),
            "run_folder": str(run_folder),
            "artifacts": {key: str(path) for key, path in artifact_paths.items()},
        }
        _write_json_artifact(artifact_paths["manifest"], manifest)
        _append_jsonl_artifact(
            artifact_paths["events"],
            {"ts": manifest["started_at"], "event": "started", "run_id": run_id},
        )

        try:
            with lerim_mlflow_run(
                enabled=self.config.mlflow_enabled,
                operation="curate",
                run_id=run_id,
                session_id=session_id,
                project_id=project_identity.project_id,
                project_name=project_identity.project_slug,
                run_folder=run_folder,
                request_preview=f"curate:{session_id}",
            ) as mlflow_run:
                result, details = run_context_curator(
                    context_db_path=self.config.context_db_path,
                    project_identity=project_identity,
                    session_id=session_id,
                    config=self.config,
                    return_details=True,
                    max_llm_calls=self.config.agent_role.curate_max_llm_calls,
                )

                response_text = (result.completion_summary or "").strip() or "(no response)"
                _write_text_with_newline(artifact_paths["agent_log"], response_text)

                try:
                    _write_graph_agent_trace(artifact_paths["agent_trace"], details)
                except Exception as exc:
                    logger.warning(f"[context-curator] Failed to write agent trace: {exc}")
                    artifact_paths["agent_trace"].write_text("[]", encoding="utf-8")

                counts = _record_change_counts(self.config, session_id)
                finished_at = datetime.now(timezone.utc).isoformat()
                records_updated = int(counts.get("update") or 0) + int(
                    counts.get("supersede") or 0
                )
                manifest["status"] = "succeeded"
                manifest["completed_at"] = finished_at
                manifest["records_created"] = int(counts.get("create") or 0)
                manifest["records_updated"] = records_updated
                manifest["records_archived"] = int(counts.get("archive") or 0)
                _write_json_artifact(artifact_paths["manifest"], manifest)
                _append_jsonl_artifact(
                    artifact_paths["events"],
                    {
                        "ts": finished_at,
                        "event": "succeeded",
                        "run_id": run_id,
                        "records_created": manifest["records_created"],
                        "records_updated": manifest["records_updated"],
                        "records_archived": manifest["records_archived"],
                    },
                )
                finish_mlflow_run(
                    mlflow_run,
                    final_status="succeeded",
                    response_preview=response_text,
                    outputs={
                        "completion_summary": response_text,
                        "records_created": manifest["records_created"],
                        "records_updated": manifest["records_updated"],
                        "records_archived": manifest["records_archived"],
                    },
                    records_created=manifest["records_created"],
                    records_updated=manifest["records_updated"],
                    records_archived=manifest["records_archived"],
                )

                payload = {
                    "context_db_path": str(self.config.context_db_path),
                    "project_id": project_identity.project_id,
                    "workspace_root": str(resolved_workspace_root),
                    "run_folder": str(run_folder),
                    "artifacts": {key: str(path) for key, path in artifact_paths.items()},
                    "records_created": manifest["records_created"],
                    "records_updated": records_updated,
                    "records_archived": manifest["records_archived"],
                    "cost_usd": 0.0,
                }
                return ContextCuratorResultContract.model_validate(payload).model_dump(
                    mode="json"
                )
        except Exception as exc:
            _mark_run_failed(
                artifact_paths=artifact_paths,
                manifest=manifest,
                exc=exc,
            )
            raise

    # ------------------------------------------------------------------
    # Context-brief flow
    # ------------------------------------------------------------------

    def context_brief(
        self,
        repo_root: str | Path | None = None,
        *,
        project_name: str | None = None,
        force: bool = False,
        trigger: str = "manual",
    ) -> dict[str, Any]:
        """Generate or skip one project's derived context brief artifact."""
        resolved_repo_root = (
            Path(repo_root).expanduser().resolve()
            if repo_root
            else Path(self._default_cwd or Path.cwd()).expanduser().resolve()
        )
        project_identity = resolve_project_identity(resolved_repo_root)
        display_name = project_name or project_identity.project_slug
        resolved_workspace_root = _resolve_runtime_roots(config=self.config)
        store = _store_for_config(self.config)
        store.register_project(project_identity)
        current_paths = context_brief_paths(
            self.config,
            project_identity.project_id,
        )
        current_manifest: dict[str, Any] = {}
        if current_paths.current_manifest.is_file():
            try:
                current_manifest = json.loads(
                    current_paths.current_manifest.read_text(encoding="utf-8")
                )
            except (OSError, json.JSONDecodeError):
                current_manifest = {}
        previous_generated_at = str(current_manifest.get("generated_at") or "").strip()
        changed_since_previous = count_changed_records_since(
            store,
            project_id=project_identity.project_id,
            since=previous_generated_at or None,
        )
        current_exists = current_paths.current_file.is_file()
        if current_exists and not force and changed_since_previous == 0:
            payload = {
                "status": "skipped",
                "project": display_name,
                "project_id": project_identity.project_id,
                "trigger": trigger,
                "generated_at": previous_generated_at or None,
                "context_db_path": str(self.config.context_db_path),
                "workspace_root": str(resolved_workspace_root),
                "run_folder": None,
                "current_file": str(current_paths.current_file),
                "current_manifest": str(current_paths.current_manifest),
                "records_considered": 0,
                "records_included": int(current_manifest.get("records_included") or 0),
                "records_changed_since_previous": 0,
                "included_record_ids": list(
                    current_manifest.get("included_record_ids") or []
                ),
                "skip_reason": "no_records_changed_since_previous_generation",
                "cost_usd": 0.0,
            }
            return ContextBriefResultContract.model_validate(payload).model_dump(
                mode="json"
            )

        run_id, run_folder = _new_run_folder(
            resolved_workspace_root,
            CONTEXT_BRIEF_OPERATION,
        )
        artifact_paths = _build_artifact_paths(run_folder, include_session_log=False)
        artifact_paths["context_brief"] = run_folder / CONTEXT_BRIEF_FILENAME
        artifact_paths["subagents_log"].write_text("", encoding="utf-8")
        started_at = utc_now_iso()
        manifest = {
            "run_id": run_id,
            "operation": CONTEXT_BRIEF_OPERATION,
            "status": "running",
            "started_at": started_at,
            "mlflow_client_request_id": run_id,
            "project_id": project_identity.project_id,
            "project": display_name,
            "repo_path": str(project_identity.repo_path),
            "workspace_root": str(resolved_workspace_root),
            "run_folder": str(run_folder),
            "artifacts": {key: str(path) for key, path in artifact_paths.items()},
        }
        _write_json_artifact(artifact_paths["manifest"], manifest)
        _append_jsonl_artifact(
            artifact_paths["events"],
            {"ts": started_at, "event": "started", "run_id": run_id},
        )

        try:
            with lerim_mlflow_run(
                enabled=self.config.mlflow_enabled,
                operation=CONTEXT_BRIEF_OPERATION,
                run_id=run_id,
                session_id=run_id,
                project_id=project_identity.project_id,
                project_name=display_name,
                run_folder=run_folder,
                request_preview=f"{CONTEXT_BRIEF_OPERATION}:{project_identity.project_id}",
            ) as mlflow_run:
                candidates = load_candidate_records(
                    store,
                    project_id=project_identity.project_id,
                )
                messages: list[Any] = []
                if candidates:
                    draft, messages = compile_context_brief(
                        config=self.config,
                        candidates=candidates,
                        return_messages=True,
                    )
                    if not draft.summary and not draft.sections:
                        raise ValueError("context_brief_empty")
                else:
                    draft = empty_context_brief_draft()
                allowed_ids = {str(record.get("record_id") or "") for record in candidates}
                record_kinds = {
                    str(record.get("record_id") or ""): str(record.get("kind") or "")
                    for record in candidates
                }
                validate_draft(
                    draft,
                    allowed_record_ids=allowed_ids,
                    record_kinds=record_kinds,
                )
                record_ids = included_record_ids(draft)
                generated_at = utc_now_iso()
                project = ContextBriefProject(
                    name=display_name,
                    identity=project_identity,
                )
                markdown = render_context_brief_markdown(
                    project=project,
                    generated_at=generated_at,
                    previous_generated_at=previous_generated_at or None,
                    generation_trigger=trigger,
                    records_considered=len(candidates),
                    records_included=len(record_ids),
                    db_records_changed_since_previous=changed_since_previous,
                    draft=draft,
                    candidate_records=candidates,
                    current_file=current_paths.current_file,
                    run_folder=run_folder,
                )
                _write_text_with_newline(artifact_paths["context_brief"], markdown)
                response_text = (
                    f"Context brief generated with {len(record_ids)} cited record(s)."
                )
                _write_text_with_newline(artifact_paths["agent_log"], response_text)
                try:
                    _write_agent_trace(artifact_paths["agent_trace"], messages)
                except Exception as exc:
                    logger.warning(f"[context-brief] Failed to write agent trace: {exc}")
                    artifact_paths["agent_trace"].write_text("[]", encoding="utf-8")

                manifest = build_manifest(
                    run_id=run_id,
                    status="succeeded",
                    generated_at=generated_at,
                    project=project,
                    records_considered=len(candidates),
                    records_included=len(record_ids),
                    included_record_ids_value=record_ids,
                    changed_records_since_previous=changed_since_previous,
                    trigger=trigger,
                    current_file=current_paths.current_file,
                    run_folder=run_folder,
                )
                manifest["completed_at"] = utc_now_iso()
                manifest["workspace_root"] = str(resolved_workspace_root)
                manifest["artifacts"] = {
                    key: str(path) for key, path in artifact_paths.items()
                }
                _write_json_artifact(artifact_paths["manifest"], manifest)
                _append_jsonl_artifact(
                    artifact_paths["events"],
                    {
                        "ts": manifest["completed_at"],
                        "event": "succeeded",
                        "run_id": run_id,
                        "records_considered": len(candidates),
                        "records_included": len(record_ids),
                        "records_changed_since_previous": changed_since_previous,
                    },
                )
                from lerim.context_brief import write_current_artifacts

                write_current_artifacts(
                    paths=current_paths,
                    run_markdown=artifact_paths["context_brief"],
                    run_manifest=artifact_paths["manifest"],
                )
                payload = {
                    "status": "generated",
                    "project": display_name,
                    "project_id": project_identity.project_id,
                    "trigger": trigger,
                    "generated_at": generated_at,
                    "context_db_path": str(self.config.context_db_path),
                    "workspace_root": str(resolved_workspace_root),
                    "run_folder": str(run_folder),
                    "current_file": str(current_paths.current_file),
                    "current_manifest": str(current_paths.current_manifest),
                    "records_considered": len(candidates),
                    "records_included": len(record_ids),
                    "records_changed_since_previous": changed_since_previous,
                    "included_record_ids": list(record_ids),
                    "skip_reason": None,
                    "cost_usd": 0.0,
                }
                finish_mlflow_run(
                    mlflow_run,
                    final_status="succeeded",
                    response_preview=response_text,
                    outputs={
                        "status": "generated",
                        "records_considered": len(candidates),
                        "records_included": len(record_ids),
                        "records_changed_since_previous": changed_since_previous,
                    },
                )
                return ContextBriefResultContract.model_validate(payload).model_dump(
                    mode="json"
                )
        except Exception as exc:
            _mark_run_failed(
                artifact_paths=artifact_paths,
                manifest=manifest,
                exc=exc,
            )
            raise

    # ------------------------------------------------------------------
    # Context-answerer flow
    # ------------------------------------------------------------------

    def answer(
        self,
        prompt: str,
        session_id: str | None = None,
        project_ids: list[str] | None = None,
        repo_root: str | Path | None = None,
        include_debug: bool = False,
    ) -> tuple[str, str, float, dict[str, Any] | None]:
        """Answer one prompt from persisted context records."""
        resolved_session_id = session_id or self.generate_session_id()
        resolved_repo_root = (
            Path(repo_root).expanduser().resolve()
            if repo_root
            else Path(self._default_cwd or Path.cwd()).expanduser().resolve()
        )
        project_identity = resolve_project_identity(resolved_repo_root)
        resolved_project_ids = project_ids or [project_identity.project_id]
        now = datetime.now(timezone.utc)
        run_id = f"answer-{now.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}"

        with lerim_mlflow_run(
            enabled=self.config.mlflow_enabled,
            operation="answer",
            run_id=run_id,
            session_id=resolved_session_id,
            project_id=project_identity.project_id,
            project_name=project_identity.project_slug,
            project_ids=resolved_project_ids,
            request_preview=prompt.strip()[:240] or f"answer:{resolved_session_id}",
        ) as mlflow_run:
            result = run_context_answerer(
                context_db_path=self.config.context_db_path,
                project_identity=project_identity,
                project_ids=resolved_project_ids,
                session_id=resolved_session_id,
                question=prompt,
                config=self.config,
                return_messages=include_debug,
            )
            debug: dict[str, Any] | None = None
            if include_debug:
                result_obj, events = result
                result = result_obj
                debug = _build_answer_debug(events)
            response_text = (result.answer or "").strip() or "(no response)"
            finish_mlflow_run(
                mlflow_run,
                final_status="succeeded",
                response_preview=response_text,
                outputs={"answer": response_text},
            )
            return response_text, resolved_session_id, 0.0, debug
