"""Submitted-trace manifests for retryable generic imports."""

from __future__ import annotations

import json
import shlex
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lerim.config.settings import Config, get_config
from lerim.traces.importer import TraceImportResult, import_trace_file

MANIFEST_SUFFIX = ".lerim-submission.json"
# 2: normalized traces are trajectory-v1 records, so version-1 manifests point at
# imports written in the retired {"type","message"} shape and cannot be retried.
SCHEMA_VERSION = 2


def submission_manifest_path(trace_path: Path) -> Path:
    """Return the sidecar manifest path for one submitted trace."""
    resolved = Path(trace_path).expanduser()
    return resolved.with_name(f"{resolved.name}{MANIFEST_SUFFIX}")


def retry_command_for(trace_path: Path) -> str:
    """Return a shell-safe retry command for a submitted trace."""
    return f"lerim trace retry {shlex.quote(str(Path(trace_path).expanduser()))}"


def create_submission_manifest(
    *,
    trace_path: Path,
    source_name: str,
    source_profile: str,
    scope_type: str,
    scope: str,
    scope_label: str | None,
    session_id: str | None,
    filename_hint: str | None,
    force: bool,
) -> dict[str, Any]:
    """Create or replace the submitted-trace manifest before import."""
    now = _utc_now()
    resolved_trace = Path(trace_path).expanduser()
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "trace_path": str(resolved_trace),
        "manifest_path": str(submission_manifest_path(resolved_trace)),
        "created_at": now,
        "updated_at": now,
        "status": "submitted",
        "source_name": str(source_name or "generic"),
        "source_profile": str(source_profile or "generic"),
        "scope_type": str(scope_type or "project"),
        "scope": str(scope),
        "scope_label": scope_label,
        "session_id": session_id,
        "filename_hint": filename_hint,
        "force": bool(force),
        "attempt_count": 0,
        "last_attempt_at": None,
        "last_error": None,
        "last_result": None,
        "retry_command": retry_command_for(resolved_trace),
    }
    _write_manifest(manifest)
    return manifest


def mark_submission_succeeded(
    *,
    trace_path: Path,
    result: TraceImportResult,
) -> dict[str, Any]:
    """Record a successful import or duplicate skip in the sidecar manifest."""
    status = str(result.ingest_result.get("status") or "imported")
    compact_result = {
        "trace_id": result.trace_id,
        "session_id": result.session_id,
        "normalized_trace_path": str(result.normalized_trace_path),
        "scope_type": result.scope_identity.scope_type,
        "scope_id": result.scope_identity.scope_id,
        "scope_label": result.scope_identity.label,
        "status": status,
        "records_created": int(result.ingest_result.get("records_created") or 0),
        "records_updated": int(result.ingest_result.get("records_updated") or 0),
        "records_archived": int(result.ingest_result.get("records_archived") or 0),
        "run_folder": str(result.ingest_result.get("run_folder") or ""),
    }
    return _update_attempt(
        trace_path=trace_path,
        status=status,
        last_error=None,
        last_result=compact_result,
    )


def mark_submission_failed(
    *,
    trace_path: Path,
    exc: Exception,
) -> dict[str, Any]:
    """Record a failed import attempt in the sidecar manifest."""
    return _update_attempt(
        trace_path=trace_path,
        status="failed",
        last_error={"type": type(exc).__name__, "message": str(exc)},
        last_result=None,
    )


def load_submission_manifest(path: Path) -> dict[str, Any]:
    """Load a submitted-trace manifest from a trace path or manifest path."""
    manifest_path = resolve_submission_manifest_path(path)
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid submission manifest: {manifest_path}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"submission manifest must be a JSON object: {manifest_path}")
    if int(data.get("schema_version") or 0) != SCHEMA_VERSION:
        raise ValueError(
            f"unsupported submission manifest schema: {data.get('schema_version')} "
            f"(expected {SCHEMA_VERSION}); resubmit the trace instead of retrying"
        )
    return data


def resolve_submission_manifest_path(path: Path) -> Path:
    """Resolve a trace or sidecar path to an existing manifest file."""
    candidate = Path(path).expanduser()
    if candidate.name.endswith(MANIFEST_SUFFIX):
        manifest_path = candidate
    else:
        manifest_path = submission_manifest_path(candidate)
    if not manifest_path.is_file():
        raise FileNotFoundError(f"submission manifest not found: {manifest_path}")
    return manifest_path


def list_submission_manifests(
    *,
    root: Path,
    status: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Return recent submitted-trace manifests from the workspace."""
    base = Path(root).expanduser() / "workspace" / "mcp-submissions"
    if not base.is_dir():
        return []
    requested_status = str(status or "").strip().lower()
    rows: list[dict[str, Any]] = []
    for manifest_path in base.rglob(f"*{MANIFEST_SUFFIX}"):
        try:
            data = load_submission_manifest(manifest_path)
        except (FileNotFoundError, ValueError):
            continue
        if requested_status and requested_status != "all":
            if str(data.get("status") or "").lower() != requested_status:
                continue
        rows.append(data)
    rows.sort(key=_submission_sort_key, reverse=True)
    return rows[: max(1, min(int(limit or 20), 500))]


def retry_submitted_trace(
    path: Path,
    *,
    force: bool = False,
    config: Config | None = None,
) -> TraceImportResult:
    """Retry importing a previously submitted trace using saved metadata."""
    cfg = config or get_config()
    manifest = load_submission_manifest(path)
    trace_path = Path(str(manifest["trace_path"])).expanduser()
    if not trace_path.is_file():
        raise FileNotFoundError(f"submitted trace not found: {trace_path}")
    try:
        result = import_trace_file(
            trace_path=trace_path,
            source_name=str(manifest.get("source_name") or "generic"),
            source_profile=str(manifest.get("source_profile") or "generic"),
            scope_type=str(manifest.get("scope_type") or "project"),
            scope=str(manifest.get("scope") or trace_path.parent),
            scope_label=_optional_str(manifest.get("scope_label")),
            session_id=_optional_str(manifest.get("session_id")),
            force=bool(force or manifest.get("force")),
            config=cfg,
        )
    except Exception as exc:
        mark_submission_failed(trace_path=trace_path, exc=exc)
        raise
    mark_submission_succeeded(trace_path=trace_path, result=result)
    return result


def _update_attempt(
    *,
    trace_path: Path,
    status: str,
    last_error: dict[str, str] | None,
    last_result: dict[str, Any] | None,
) -> dict[str, Any]:
    """Update attempt metadata while preserving submission arguments."""
    manifest_path = submission_manifest_path(trace_path)
    try:
        manifest = load_submission_manifest(manifest_path)
    except FileNotFoundError:
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "trace_path": str(Path(trace_path).expanduser()),
            "manifest_path": str(manifest_path),
            "created_at": _utc_now(),
            "retry_command": retry_command_for(trace_path),
        }
    manifest["updated_at"] = _utc_now()
    manifest["status"] = status
    manifest["attempt_count"] = int(manifest.get("attempt_count") or 0) + 1
    manifest["last_attempt_at"] = manifest["updated_at"]
    manifest["last_error"] = last_error
    manifest["last_result"] = last_result
    manifest["retry_command"] = retry_command_for(trace_path)
    _write_manifest(manifest)
    return manifest


def _write_manifest(manifest: dict[str, Any]) -> None:
    """Write one manifest as deterministic JSON."""
    path = Path(str(manifest["manifest_path"])).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(_jsonable(manifest), indent=2, sort_keys=True, ensure_ascii=True)
        + "\n",
        encoding="utf-8",
    )


def _jsonable(value: Any) -> Any:
    """Convert path-like values into JSON-safe primitives."""
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonable(item) for item in value]
    return value


def _optional_str(value: Any) -> str | None:
    """Return a string only when the manifest value is present."""
    if value is None:
        return None
    text = str(value)
    return text if text else None


def _submission_sort_key(row: dict[str, Any]) -> tuple[str, str]:
    """Sort submitted traces by updated timestamp then path."""
    return (
        str(row.get("updated_at") or row.get("created_at") or ""),
        str(row.get("trace_path") or ""),
    )


def _utc_now() -> str:
    """Return an ISO-8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def submission_status_counts(rows: Iterable[dict[str, Any]]) -> dict[str, int]:
    """Summarize manifests by current status."""
    counts: dict[str, int] = {}
    for row in rows:
        status = str(row.get("status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
    return counts
