"""Runtime orchestrator for Lerim sync, maintain, and ask.

Sync uses the BAML/LangGraph extract harness. Maintain, ask, and working-memory
synthesis still use PydanticAI until those flows are migrated.
"""

from __future__ import annotations

import json
import logging
import secrets
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from lerim.agents.ask import run_ask
from lerim.agents.contracts import (
    MaintainResultContract,
    SyncResultContract,
    WorkingMemoryResultContract,
)
from lerim.agents.extract import ExtractionRunDetails, run_extraction
from lerim.agents.maintain import run_maintain
from lerim.agents.mlflow_observability import finish_mlflow_run, lerim_mlflow_run
from lerim.agents.working_memory import run_working_memory_synthesis
from lerim.config.providers import build_pydantic_model
from lerim.config.settings import Config, get_config
from lerim.context import resolve_project_identity
from lerim.working_memory import (
    WORKING_MEMORY_FILENAME,
    WORKING_MEMORY_OPERATION,
    build_manifest,
    count_changed_records_since,
    empty_working_memory_draft,
    included_record_ids,
    load_candidate_records,
    render_working_memory_markdown,
    utc_now_iso,
    validate_draft,
    WorkingMemoryProject,
    working_memory_paths,
)
from pydantic_ai.messages import ModelMessage, ModelMessagesTypeAdapter

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


def _write_agent_trace(path: Path, messages: list[ModelMessage]) -> None:
    """Serialize PydanticAI message history to a stable JSON artifact."""
    trace_data = ModelMessagesTypeAdapter.dump_python(messages, mode="json")
    path.write_text(json.dumps(trace_data, indent=2), encoding="utf-8")


def _write_extract_agent_trace(path: Path, details: ExtractionRunDetails) -> None:
    """Serialize BAML/LangGraph extract events to a stable JSON artifact."""
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


@contextmanager
def _mlflow_run_span(
    *,
    enabled: bool,
    operation: str,
    run_id: str,
    session_id: str,
    project_id: str,
    project_name: str,
    run_folder: Path,
) -> Iterator[None]:
    """Create an MLflow trace wrapper tagged with Lerim's run identity."""
    if not enabled:
        yield
        return
    try:
        import mlflow
    except Exception as exc:
        logger.warning(f"[{operation}] MLflow run span unavailable: {exc}")
        yield
        return

    span_cm = mlflow.start_span(
        name=f"lerim.{operation}",
        span_type="CHAIN",
        attributes={
            "lerim.run_id": run_id,
            "lerim.operation": operation,
            "lerim.session_id": session_id,
            "lerim.project_id": project_id,
            "lerim.project": project_name,
            "lerim.run_folder": str(run_folder),
        },
    )
    try:
        span_cm.__enter__()
        mlflow.update_current_trace(
            client_request_id=run_id,
            tags={
                "lerim.run_id": run_id,
                "lerim.operation": operation,
                "lerim.session_id": session_id,
                "lerim.project_id": project_id,
                "lerim.project": project_name,
            },
            metadata={"lerim.run_folder": str(run_folder)},
            request_preview=f"{operation}:{session_id}",
        )
    except Exception as exc:
        try:
            span_cm.__exit__(type(exc), exc, exc.__traceback__)
        except Exception:
            pass
        logger.warning(f"[{operation}] MLflow run span unavailable: {exc}")
        yield
        return

    try:
        yield
    except BaseException as exc:
        span_cm.__exit__(type(exc), exc, exc.__traceback__)
        raise
    else:
        span_cm.__exit__(None, None, None)


def _build_ask_debug(messages: list[ModelMessage]) -> dict[str, Any]:
    """Build a sanitized ask debug payload from message history."""
    trace_data = ModelMessagesTypeAdapter.dump_python(messages, mode="json")
    tool_calls: list[dict[str, Any]] = []
    tool_results: list[dict[str, Any]] = []
    assistant_texts: list[str] = []
    ordered_messages: list[dict[str, Any]] = []

    for message_index, message in enumerate(trace_data):
        sanitized_parts: list[dict[str, Any]] = []
        for part in message.get("parts", []) or []:
            part_kind = str(part.get("part_kind") or "")
            if part_kind == "tool-call":
                entry = {
                    "part_kind": part_kind,
                    "tool_name": str(part.get("tool_name") or ""),
                    "args": part.get("args"),
                    "tool_call_id": part.get("tool_call_id"),
                }
                sanitized_parts.append(entry)
                tool_calls.append(
                    {
                        "tool_name": entry["tool_name"],
                        "args": entry["args"],
                        "tool_call_id": entry["tool_call_id"],
                    }
                )
                continue
            if part_kind == "tool-return":
                content = part.get("content")
                text = (
                    content
                    if isinstance(content, str)
                    else json.dumps(content, ensure_ascii=True)
                )
                entry = {
                    "part_kind": part_kind,
                    "tool_name": str(part.get("tool_name") or ""),
                    "tool_call_id": part.get("tool_call_id"),
                    "is_error": bool(part.get("is_error")),
                    "content_preview": text[:200],
                }
                sanitized_parts.append(entry)
                tool_results.append(
                    {
                        "tool_name": entry["tool_name"],
                        "tool_call_id": entry["tool_call_id"],
                        "is_error": entry["is_error"],
                        "content_preview": entry["content_preview"],
                    }
                )
                continue
            if part_kind == "system-prompt":
                sanitized_parts.append(
                    {
                        "part_kind": part_kind,
                        "char_count": len(str(part.get("content") or "")),
                    }
                )
                continue
            if part_kind == "user-prompt":
                sanitized_parts.append(
                    {
                        "part_kind": part_kind,
                        "content": str(part.get("content") or ""),
                    }
                )
                continue
            if part_kind == "text":
                text = str(part.get("content") or "").strip()
                if text:
                    assistant_texts.append(text)
                    sanitized_parts.append(
                        {
                            "part_kind": part_kind,
                            "content": text,
                        }
                    )
                continue
            if part_kind == "thinking":
                continue
            sanitized_parts.append(
                {
                    "part_kind": part_kind,
                    "content": str(part)[:1000],
                }
            )

        ordered_messages.append(
            {
                "message_index": message_index,
                "kind": str(message.get("kind") or ""),
                "parts": sanitized_parts,
            }
        )

    return {
        "tool_calls": tool_calls,
        "tool_results": tool_results,
        "assistant_texts": assistant_texts,
        "message_count": len(trace_data),
        "messages": ordered_messages,
    }


# ---------------------------------------------------------------------------
# Quota error detection (PydanticAI path)
# ---------------------------------------------------------------------------


def _is_quota_error_pydantic(exc: Exception) -> bool:
    """Detect rate-limit / quota errors across PydanticAI provider backends."""
    try:
        from openai import APIStatusError, RateLimitError
    except ImportError:
        RateLimitError = APIStatusError = None
    try:
        from httpx import HTTPStatusError
    except ImportError:
        HTTPStatusError = None

    if RateLimitError is not None and isinstance(exc, RateLimitError):
        return True
    if (
        APIStatusError is not None
        and isinstance(exc, APIStatusError)
        and getattr(exc, "status_code", None) == 429
    ):
        return True
    if HTTPStatusError is not None and isinstance(exc, HTTPStatusError):
        try:
            if exc.response.status_code == 429:
                return True
        except Exception:
            pass

    msg = str(exc).lower()
    return "429" in msg or "rate limit" in msg or "quota" in msg


class LerimRuntime:
    """Runtime orchestrator for sync, maintain, ask, and working memory."""

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
        """Generate a unique session ID for ask mode."""
        return f"lerim-{secrets.token_hex(6)}"

    def _run_with_fallback(
        self,
        *,
        flow: str,
        callable_fn: Callable[[Any], Any],
        model_builders: list[Callable[[], Any]],
        max_attempts: int = 3,
    ) -> Any:
        """Run a PydanticAI callable with retry + model-builder fallback support."""
        from pydantic_ai.exceptions import UsageLimitExceeded

        last_exc: Exception | None = None
        for model_idx, builder in enumerate(model_builders):
            model_label = (
                self.config.agent_role.model
                if model_idx == 0
                else f"fallback-{model_idx}"
            )
            for attempt in range(1, max_attempts + 1):
                try:
                    logger.info(
                        f"[{flow}] pydantic-ai attempt {attempt}/{max_attempts} "
                        f"(model={model_label})"
                    )
                    model = builder()
                    return callable_fn(model)
                except UsageLimitExceeded as exc:
                    logger.warning(
                        f"[{flow}] usage limit exceeded, short-circuiting: {exc}"
                    )
                    raise
                except Exception as exc:
                    last_exc = exc
                    if isinstance(exc, ValueError):
                        logger.error(
                            f"[{flow}] non-retryable agent/store error: {str(exc)[:100]}"
                        )
                        raise
                    if _is_quota_error_pydantic(exc):
                        logger.warning(
                            f"[{flow}] quota error on {model_label}: {str(exc)[:100]}"
                        )
                        break
                    if attempt < max_attempts:
                        wait_time = min(2**attempt, 8)
                        logger.warning(
                            f"[{flow}] transient error on attempt {attempt}/{max_attempts} "
                            f"({type(exc).__name__}): {str(exc)[:100]}; retrying in {wait_time}s..."
                        )
                        time.sleep(wait_time)
                        continue
                    logger.error(
                        f"[{flow}] exhausted retries on {model_label}: {str(exc)[:100]}"
                    )
                    break

        raise RuntimeError(
            f"[{flow}] Failed after trying {len(model_builders)} model(s). "
            f"Last error: {last_exc}"
        ) from last_exc

    # ------------------------------------------------------------------
    # Sync flow
    # ------------------------------------------------------------------

    def sync(
        self,
        trace_path: str | Path,
        session_id: str | None = None,
        agent_type: str = "unknown",
        session_meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Run record-write sync flow and return stable contract payload."""
        trace_file = Path(trace_path).expanduser().resolve()
        if not trace_file.exists() or not trace_file.is_file():
            raise FileNotFoundError(f"trace_path_missing:{trace_file}")

        repo_root = Path(self._default_cwd or Path.cwd()).expanduser().resolve()
        return self._sync_inner(
            trace_file,
            repo_root,
            session_id=session_id,
            agent_type=agent_type,
            session_meta=session_meta or {},
        )

    def _sync_inner(
        self,
        trace_file: Path,
        repo_root: Path,
        *,
        session_id: str | None,
        agent_type: str,
        session_meta: dict[str, Any],
    ) -> dict[str, Any]:
        """Inner sync logic called by sync()."""
        project_identity = resolve_project_identity(repo_root)
        resolved_workspace_root = _resolve_runtime_roots(config=self.config)
        store = _store_for_config(self.config)
        store.register_project(project_identity)
        resolved_session_id = session_id or trace_file.stem
        store.upsert_session(
            project_id=project_identity.project_id,
            session_id=resolved_session_id,
            agent_type=agent_type,
            source_trace_ref=str(trace_file),
            repo_path=str(project_identity.repo_path),
            cwd=str(session_meta.get("cwd") or project_identity.repo_path),
            started_at=str(session_meta.get("started_at") or ""),
            model_name=str(self.config.agent_role.model),
            instructions_text=str(session_meta.get("instructions_text") or "")[:4000]
            or None,
            prompt_text=str(session_meta.get("prompt_text") or "")[:4000] or None,
            metadata=session_meta,
        )

        run_id, run_folder = _new_run_folder(resolved_workspace_root, "sync")
        artifact_paths = _build_artifact_paths(run_folder, include_session_log=True)
        metadata = {
            "run_id": run_id,
            "trace_path": str(trace_file),
            "repo_name": repo_root.name,
        }
        _write_json_artifact(artifact_paths["session_log"], metadata)
        artifact_paths["subagents_log"].write_text("", encoding="utf-8")
        manifest = {
            "run_id": run_id,
            "operation": "sync",
            "status": "running",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "mlflow_client_request_id": run_id,
            "session_id": resolved_session_id,
            "agent_type": agent_type,
            "trace_path": str(trace_file),
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
                operation="sync",
                run_id=run_id,
                session_id=resolved_session_id,
                project_id=project_identity.project_id,
                project_name=project_identity.project_slug,
                run_folder=run_folder,
                request_preview=f"sync:{resolved_session_id}",
            ) as mlflow_run:
                result, details = run_extraction(
                    context_db_path=self.config.context_db_path,
                    project_identity=project_identity,
                    session_id=resolved_session_id,
                    trace_path=trace_file,
                    config=self.config,
                    session_started_at=str(session_meta.get("started_at") or ""),
                    return_details=True,
                )

                response_text = (result.completion_summary or "").strip() or "(no response)"
                _write_text_with_newline(artifact_paths["agent_log"], response_text)

                try:
                    _write_extract_agent_trace(artifact_paths["agent_trace"], details)
                except Exception as exc:
                    logger.warning(f"[sync] Failed to write agent trace: {exc}")
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
                    "project_id": project_identity.project_id,
                    "workspace_root": str(resolved_workspace_root),
                    "run_folder": str(run_folder),
                    "artifacts": {key: str(path) for key, path in artifact_paths.items()},
                    "records_created": manifest["records_created"],
                    "records_updated": records_updated,
                    "records_archived": manifest["records_archived"],
                    "cost_usd": 0.0,
                }
                return SyncResultContract.model_validate(payload).model_dump(mode="json")
        except Exception as exc:
            _mark_run_failed(
                artifact_paths=artifact_paths,
                manifest=manifest,
                exc=exc,
            )
            raise

    # ------------------------------------------------------------------
    # Maintain flow
    # ------------------------------------------------------------------

    def maintain(
        self,
        repo_root: str | Path | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Run context-store maintenance flow and return stable contract payload."""
        resolved_repo_root = (
            Path(repo_root).expanduser().resolve()
            if repo_root
            else Path(self._default_cwd or Path.cwd()).expanduser().resolve()
        )
        return self._maintain_inner(
            resolved_repo_root,
            session_id=session_id or self.generate_session_id(),
        )

    def _maintain_inner(
        self,
        repo_root: Path,
        *,
        session_id: str,
    ) -> dict[str, Any]:
        """Inner maintain logic called by maintain()."""
        project_identity = resolve_project_identity(repo_root)
        resolved_workspace_root = _resolve_runtime_roots(config=self.config)
        store = _store_for_config(self.config)
        store.register_project(project_identity)
        store.upsert_session(
            project_id=project_identity.project_id,
            session_id=session_id,
            agent_type="maintain",
            source_trace_ref=f"maintain:{project_identity.project_id}",
            repo_path=str(project_identity.repo_path),
            cwd=str(project_identity.repo_path),
            started_at=datetime.now(timezone.utc).isoformat(),
            model_name=str(self.config.agent_role.model),
            instructions_text=None,
            prompt_text=None,
            metadata={},
        )

        run_id, run_folder = _new_run_folder(resolved_workspace_root, "maintain")
        artifact_paths = _build_artifact_paths(run_folder, include_session_log=False)
        artifact_paths["subagents_log"].write_text("", encoding="utf-8")
        manifest = {
            "run_id": run_id,
            "operation": "maintain",
            "status": "running",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "mlflow_client_request_id": run_id,
            "session_id": session_id,
            "agent_type": "maintain",
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

        def _primary_builder() -> Any:
            return build_pydantic_model("agent", config=self.config)

        def _call(model: Any) -> tuple[Any, list[ModelMessage]]:
            return run_maintain(
                context_db_path=self.config.context_db_path,
                project_identity=project_identity,
                session_id=session_id,
                model=model,
                request_limit=self.config.agent_role.max_iters_maintain,
                return_messages=True,
            )

        try:
            with lerim_mlflow_run(
                enabled=self.config.mlflow_enabled,
                operation="maintain",
                run_id=run_id,
                session_id=session_id,
                project_id=project_identity.project_id,
                project_name=project_identity.project_slug,
                run_folder=run_folder,
                request_preview=f"maintain:{session_id}",
            ) as mlflow_run:
                result, messages = self._run_with_fallback(
                    flow="maintain",
                    callable_fn=_call,
                    model_builders=[_primary_builder],
                )

                response_text = (result.completion_summary or "").strip() or "(no response)"
                _write_text_with_newline(artifact_paths["agent_log"], response_text)

                try:
                    _write_agent_trace(artifact_paths["agent_trace"], messages)
                except Exception as exc:
                    logger.warning(f"[maintain] Failed to write agent trace: {exc}")
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
                return MaintainResultContract.model_validate(payload).model_dump(
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
    # Working Memory flow
    # ------------------------------------------------------------------

    def working_memory(
        self,
        repo_root: str | Path | None = None,
        *,
        project_name: str | None = None,
        force: bool = False,
        trigger: str = "manual",
    ) -> dict[str, Any]:
        """Generate or skip one project's derived Working Memory artifact."""
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
        current_paths = working_memory_paths(
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
            return WorkingMemoryResultContract.model_validate(payload).model_dump(
                mode="json"
            )

        run_id, run_folder = _new_run_folder(
            resolved_workspace_root,
            WORKING_MEMORY_OPERATION,
        )
        artifact_paths = _build_artifact_paths(run_folder, include_session_log=False)
        artifact_paths["working_memory"] = run_folder / WORKING_MEMORY_FILENAME
        artifact_paths["subagents_log"].write_text("", encoding="utf-8")
        started_at = utc_now_iso()
        manifest = {
            "run_id": run_id,
            "operation": WORKING_MEMORY_OPERATION,
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
            candidates = load_candidate_records(
                store,
                project_id=project_identity.project_id,
            )
            messages: list[ModelMessage] = []
            if candidates:
                def _primary_builder() -> Any:
                    return build_pydantic_model("agent", config=self.config)

                def _call(model: Any) -> tuple[Any, list[ModelMessage]]:
                    return run_working_memory_synthesis(
                        model=model,
                        candidates=candidates,
                        return_messages=True,
                    )

                draft, messages = self._run_with_fallback(
                    flow=WORKING_MEMORY_OPERATION,
                    callable_fn=_call,
                    model_builders=[_primary_builder],
                )
                if not draft.summary and not draft.sections:
                    raise ValueError("working_memory_synthesis_empty")
            else:
                draft = empty_working_memory_draft()
            allowed_ids = {str(record.get("record_id") or "") for record in candidates}
            validate_draft(draft, allowed_record_ids=allowed_ids)
            record_ids = included_record_ids(draft)
            generated_at = utc_now_iso()
            project = WorkingMemoryProject(
                name=display_name,
                identity=project_identity,
            )
            markdown = render_working_memory_markdown(
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
            _write_text_with_newline(artifact_paths["working_memory"], markdown)
            response_text = (
                f"Working Memory generated with {len(record_ids)} cited record(s)."
            )
            _write_text_with_newline(artifact_paths["agent_log"], response_text)
            try:
                _write_agent_trace(artifact_paths["agent_trace"], messages)
            except Exception as exc:
                logger.warning(f"[working-memory] Failed to write agent trace: {exc}")
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
            from lerim.working_memory import write_current_artifacts

            write_current_artifacts(
                paths=current_paths,
                run_markdown=artifact_paths["working_memory"],
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
            return WorkingMemoryResultContract.model_validate(payload).model_dump(
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
    # Ask flow
    # ------------------------------------------------------------------

    def ask(
        self,
        prompt: str,
        session_id: str | None = None,
        project_ids: list[str] | None = None,
        repo_root: str | Path | None = None,
        include_debug: bool = False,
    ) -> tuple[str, str, float, dict[str, Any] | None]:
        """Run one ask prompt. Returns (response, session_id, cost_usd, debug)."""
        resolved_session_id = session_id or self.generate_session_id()
        resolved_repo_root = (
            Path(repo_root).expanduser().resolve()
            if repo_root
            else Path(self._default_cwd or Path.cwd()).expanduser().resolve()
        )
        project_identity = resolve_project_identity(resolved_repo_root)
        resolved_project_ids = project_ids or [project_identity.project_id]
        now = datetime.now(timezone.utc)
        run_id = f"ask-{now.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}"

        def _primary_builder() -> Any:
            return build_pydantic_model("agent", config=self.config)

        def _call(model: Any) -> Any:
            return run_ask(
                context_db_path=self.config.context_db_path,
                project_identity=project_identity,
                project_ids=resolved_project_ids,
                session_id=resolved_session_id,
                model=model,
                question=prompt,
                request_limit=self.config.agent_role.max_iters_ask,
                return_messages=include_debug,
            )

        with lerim_mlflow_run(
            enabled=self.config.mlflow_enabled,
            operation="ask",
            run_id=run_id,
            session_id=resolved_session_id,
            project_id=project_identity.project_id,
            project_name=project_identity.project_slug,
            project_ids=resolved_project_ids,
            request_preview=prompt.strip()[:240] or f"ask:{resolved_session_id}",
        ) as mlflow_run:
            result = self._run_with_fallback(
                flow="ask",
                callable_fn=_call,
                model_builders=[_primary_builder],
            )
            debug: dict[str, Any] | None = None
            if include_debug:
                result_obj, messages = result
                result = result_obj
                debug = _build_ask_debug(messages)
            response_text = (result.answer or "").strip() or "(no response)"
            finish_mlflow_run(
                mlflow_run,
                final_status="succeeded",
                response_preview=response_text,
                outputs={"answer": response_text},
            )
            return response_text, resolved_session_id, 0.0, debug
