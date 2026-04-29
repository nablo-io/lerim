"""Sync/maintain daemon orchestration, locking, and service run reporting."""

from __future__ import annotations

import json
import os
import sqlite3
import socket
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from lerim.config.project_scope import match_session_project
from lerim.config.logging import log_file_path
from lerim.config.settings import get_config, reload_config
from lerim.server.runtime import LerimRuntime
from lerim.sessions.catalog import (
    DEFAULT_RUNNING_JOB_LEASE_SECONDS,
    IndexedSession,
    claim_session_jobs,
    complete_session_job,
    enqueue_session_job,
    fail_session_job,
    fetch_session_doc,
    heartbeat_session_job,
    index_new_sessions,
    reap_stale_running_jobs,
    record_service_run,
)


ACTIVITY_LOG_PATH: Path | None = None


def log_activity(
    op: str, project: str, stats: str, duration_s: float, cost_usd: float = 0.0
) -> None:
    """Append one line to the dated activity log.

    Format: ``2026-03-01 14:23:05 | sync | myproject | 3 new, 1 updated | $0.0042 | 4.2s``
    """
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    cost_str = f"${cost_usd:.4f}"
    line = f"{ts} | {op:<8} | {project} | {stats} | {cost_str} | {duration_s:.1f}s\n"
    log_path = ACTIVITY_LOG_PATH or log_file_path("activity.log")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a") as f:
        f.write(line)


@dataclass
class OperationResult:
    """Unified result payload for sync and maintain operations."""

    operation: str  # "sync" or "maintain"
    status: str  # "completed", "partial", "failed", "lock_busy"
    trigger: str  # "daemon", "manual", "api"

    # Sync-specific
    indexed_sessions: int = 0
    queued_sessions: int = 0
    extracted_sessions: int = 0
    skipped_sessions: int = 0
    skipped_unscoped: int = 0
    failed_sessions: int = 0
    run_ids: list[str] = field(default_factory=list)
    window_start: str | None = None
    window_end: str | None = None

    # Maintain-specific
    projects: dict[str, Any] = field(default_factory=dict)
    maintain_metrics: dict[str, Any] = field(default_factory=dict)

    # Shared structured telemetry (v1)
    metrics_version: int = 1
    sync_metrics: dict[str, Any] = field(default_factory=dict)
    projects_metrics: dict[str, Any] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)

    # Shared
    cost_usd: float = 0.0
    error: str | None = None
    dry_run: bool = False

    def to_details_json(self) -> dict[str, Any]:
        """Serialize for service_runs.details_json storage.

        Strips operation/status/trigger (already separate columns in service_runs)
        and None values to keep the JSON compact.
        """
        d = asdict(self)
        out: dict[str, Any] = {}
        structured_only_keys = {
            "indexed_sessions",
            "queued_sessions",
            "extracted_sessions",
            "skipped_sessions",
            "skipped_unscoped",
            "failed_sessions",
            "run_ids",
            "window_start",
            "window_end",
            "projects",
        }
        for k, v in d.items():
            if k in ("operation", "status", "trigger") or k in structured_only_keys:
                continue
            if k in (
                "metrics_version",
                "sync_metrics",
                "maintain_metrics",
                "projects_metrics",
                "events",
            ):
                out[k] = v
                continue
            if v is not None and v != 0 and v != [] and v != {} and v is not False:
                out[k] = v

        return out

    def to_response_json(self) -> dict[str, Any]:
        """Serialize the direct CLI/API operation response payload."""
        out = self.to_details_json()
        if self.projects:
            out["projects"] = self.projects
        return out

    def to_span_attrs(self) -> dict[str, Any]:
        """Return flat key-value attributes for Logfire span."""
        attrs: dict[str, Any] = {
            "operation": self.operation,
            "status": self.status,
            "trigger": self.trigger,
        }
        if self.operation == "sync":
            attrs["indexed_sessions"] = self.indexed_sessions
            attrs["extracted_sessions"] = self.extracted_sessions
            attrs["skipped_unscoped"] = self.skipped_unscoped
            attrs["failed_sessions"] = self.failed_sessions
        elif self.operation == "maintain":
            attrs["projects_count"] = len(self.projects)
        if self.cost_usd:
            attrs["cost_usd"] = self.cost_usd
        if self.error:
            attrs["error"] = self.error
        return attrs


EXIT_OK = 0
EXIT_FATAL = 1
EXIT_PARTIAL = 3
EXIT_LOCK_BUSY = 4
WRITER_LOCK_NAME = "writer.lock"
RUNNING_JOB_LEASE_SECONDS = DEFAULT_RUNNING_JOB_LEASE_SECONDS


def lock_path(name: str) -> Path:
    """Return lock file path under global index directory.

    Lock files must be co-located with the sessions DB they protect,
    always in ~/.lerim/index/ regardless of CWD.
    """
    return get_config().global_data_dir / "index" / name


def _parse_iso(raw: str | None) -> datetime | None:
    """Parse ISO timestamp strings safely."""
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _retry_backoff_seconds(attempts: int) -> int:
    """Return bounded exponential retry backoff in seconds."""
    safe_attempts = max(attempts, 1)
    return min(3600, 30 * (2 ** (safe_attempts - 1)))


def _start_job_heartbeat(run_id: str, interval_seconds: int = 30) -> threading.Event:
    """Refresh a running queue job lease until the returned event is set."""
    stop = threading.Event()
    heartbeat_session_job(run_id)

    def _beat() -> None:
        while not stop.wait(max(1, int(interval_seconds))):
            if not heartbeat_session_job(run_id):
                return

    threading.Thread(
        target=_beat,
        name=f"lerim-job-heartbeat-{run_id[:12]}",
        daemon=True,
    ).start()
    return stop


def _now_iso() -> str:
    """Return current UTC timestamp in ISO-8601 format."""
    return datetime.now(timezone.utc).isoformat()


def _empty_sync_summary() -> SyncSummary:
    """Return an empty sync summary payload."""
    return SyncSummary(
        indexed_sessions=0,
        extracted_sessions=0,
        skipped_sessions=0,
        skipped_unscoped=0,
        failed_sessions=0,
        run_ids=[],
    )


def _record_service_event(
    record_fn: Callable[..., Any],
    *,
    job_type: str,
    status: str,
    started_at: str,
    trigger: str,
    details: dict[str, Any],
) -> None:
    """Record a service run with canonical completed timestamp."""
    record_fn(
        job_type=job_type,
        status=status,
        started_at=started_at,
        completed_at=_now_iso(),
        trigger=trigger,
        details=details,
    )


def _pid_alive(pid: int | None) -> bool:
    """Return whether a PID appears alive on this host."""
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _process_start_ticks(pid: int) -> str | None:
    """Return the Linux process start tick for PID reuse detection."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        return stat.rsplit(") ", 1)[1].split()[19]
    except IndexError:
        return None


def _pid_matches_lock_state(state: dict[str, object]) -> bool:
    """Return whether the lock PID still refers to the recorded process."""
    pid = state.get("pid")
    if not isinstance(pid, int) or not _pid_alive(pid):
        return False
    recorded_start = state.get("process_start_ticks")
    if isinstance(recorded_start, str) and recorded_start:
        current_start = _process_start_ticks(pid)
        if current_start and current_start != recorded_start:
            return False
    return True


def _is_stale(state: dict[str, object], stale_seconds: int) -> bool:
    """Return whether lock heartbeat state is stale."""
    heartbeat = _parse_iso(str(state.get("heartbeat_at") or ""))
    if not heartbeat:
        return True
    elapsed = (datetime.now(timezone.utc) - heartbeat).total_seconds()
    return elapsed > max(stale_seconds, 1)


def read_json_file(path: Path) -> dict[str, object] | None:
    """Read a JSON object file; return ``None`` on failures."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def active_lock_state(path: Path, stale_seconds: int = 60) -> dict[str, object] | None:
    """Return active non-stale lock state or ``None`` when stale/missing."""
    state = read_json_file(path)
    if not state:
        return None
    if _pid_matches_lock_state(state) and not _is_stale(state, stale_seconds):
        return state
    return None


@dataclass
class LockBusyError(RuntimeError):
    """Raised when a service lock is currently held by another live process."""

    lock_path: Path
    state: dict[str, object] | None = None

    def __str__(self) -> str:
        """Render lock owner details for user-facing errors."""
        if self.state:
            owner = self.state.get("owner") or "unknown"
            pid = self.state.get("pid") or "unknown"
            return f"lock busy: {self.lock_path} (owner={owner}, pid={pid})"
        return f"lock busy: {self.lock_path}"


class ServiceLock:
    """Filesystem lock helper with stale lock reclamation."""

    def __init__(self, path: Path, stale_seconds: int = 60) -> None:
        """Store lock path and stale threshold for acquire/release calls."""
        self.path = path
        self.stale_seconds = stale_seconds
        self._held = False
        self._state: dict[str, object] | None = None
        self._stop_heartbeat: threading.Event | None = None
        self._heartbeat_thread: threading.Thread | None = None

    def acquire(self, owner: str, command: str) -> dict[str, object]:
        """Acquire lock file or raise ``LockBusyError`` if still active."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            process_start_ticks = _process_start_ticks(os.getpid())
            state: dict[str, object] = {
                "pid": os.getpid(),
                "owner": owner,
                "command": command,
                "started_at": datetime.now(timezone.utc).isoformat(),
                "heartbeat_at": datetime.now(timezone.utc).isoformat(),
                "host": socket.gethostname() or "local",
            }
            if process_start_ticks:
                state["process_start_ticks"] = process_start_ticks
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(json.dumps(state, ensure_ascii=True, indent=2))
                    handle.write("\n")
                self._held = True
                self._state = state
                self._start_heartbeat()
                return state
            except FileExistsError:
                active = active_lock_state(self.path, stale_seconds=self.stale_seconds)
                if active:
                    raise LockBusyError(self.path, active)
                try:
                    self.path.unlink(missing_ok=True)
                except OSError:
                    raise LockBusyError(self.path, read_json_file(self.path))
        raise LockBusyError(self.path, read_json_file(self.path))

    def _start_heartbeat(self) -> None:
        """Refresh the lock heartbeat while the current process owns it."""
        if not self._held or self._state is None:
            return
        interval = max(1, min(30, self.stale_seconds // 3 or 1))
        stop = threading.Event()
        self._stop_heartbeat = stop

        def _beat() -> None:
            while not stop.wait(interval):
                state = read_json_file(self.path)
                if not state or state.get("pid") != os.getpid():
                    return
                updated = dict(self._state or {})
                updated["heartbeat_at"] = datetime.now(timezone.utc).isoformat()
                try:
                    self.path.write_text(
                        json.dumps(updated, ensure_ascii=True, indent=2) + "\n",
                        encoding="utf-8",
                    )
                except OSError:
                    return
                self._state = updated

        self._heartbeat_thread = threading.Thread(
            target=_beat,
            name=f"lerim-lock-heartbeat-{self.path.name}",
            daemon=True,
        )
        self._heartbeat_thread.start()

    def release(self) -> None:
        """Release lock only when held by current process."""
        if not self._held:
            return
        if self._stop_heartbeat is not None:
            self._stop_heartbeat.set()
        if (
            self._heartbeat_thread is not None
            and self._heartbeat_thread is not threading.current_thread()
        ):
            self._heartbeat_thread.join(timeout=2)
        state = read_json_file(self.path)
        if state and state.get("pid") != os.getpid():
            self._held = False
            return
        try:
            self.path.unlink(missing_ok=True)
        except OSError:
            pass
        self._held = False
        self._state = None
        self._stop_heartbeat = None
        self._heartbeat_thread = None


@dataclass(frozen=True)
class SyncSummary:
    """Summary payload for one sync execution."""

    indexed_sessions: int
    extracted_sessions: int
    skipped_sessions: int
    failed_sessions: int
    run_ids: list[str]
    cost_usd: float = 0.0
    skipped_unscoped: int = 0


def resolve_window_bounds(
    *,
    window: str | None,
    since_raw: str | None,
    until_raw: str | None,
    parse_duration_to_seconds: Callable[[str], int],
) -> tuple[datetime | None, datetime]:
    """Resolve sync/maintain time window from CLI arguments."""
    now = datetime.now(timezone.utc)
    since = _parse_iso(since_raw)
    until = _parse_iso(until_raw) or now
    if since and since.tzinfo is None:
        since = since.replace(tzinfo=timezone.utc)
    if until.tzinfo is None:
        until = until.replace(tzinfo=timezone.utc)
    if window and (since_raw or until_raw):
        raise ValueError("--window cannot be combined with --since/--until")
    if since and since > until:
        raise ValueError("--since must be before --until")
    if since:
        return since, until

    if not window:
        days = get_config().sync_window_days
        seconds = parse_duration_to_seconds(f"{days}d")
        return until - timedelta(seconds=seconds), until
    if window == "all":
        try:
            with sqlite3.connect(get_config().sessions_db_path) as conn:
                row = conn.execute(
                    "SELECT MIN(start_time) FROM session_docs WHERE start_time IS NOT NULL AND start_time != ''"
                ).fetchone()
            start_raw = row[0] if row else None
        except sqlite3.Error:
            start_raw = None
        if not start_raw:
            return None, until
        parsed = _parse_iso(str(start_raw))
        if not parsed:
            return None, until
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed, until
    seconds = parse_duration_to_seconds(window)
    return until - timedelta(seconds=seconds), until


def _new_project_metric() -> dict[str, Any]:
    """Create empty per-project metrics row."""
    return {
        "sessions_analyzed": 0,
        "sessions_extracted": 0,
        "sessions_failed": 0,
        "sessions_skipped": 0,
        "records_created": 0,
        "records_updated": 0,
        "records_archived": 0,
        "duration_ms": 0,
        "last_error": None,
        "maintain_counts": {
            "merged": 0,
            "archived": 0,
            "consolidated": 0,
            "unchanged": 0,
        },
    }


def _merge_project_metric(target: dict[str, Any], source: dict[str, Any]) -> None:
    """Merge one per-project metric row into another."""
    for key in (
        "sessions_analyzed",
        "sessions_extracted",
        "sessions_failed",
        "sessions_skipped",
        "records_created",
        "records_updated",
        "records_archived",
        "duration_ms",
    ):
        target[key] = int(target.get(key) or 0) + int(source.get(key) or 0)
    if source.get("last_error"):
        target["last_error"] = str(source.get("last_error"))
    t_counts = (
        target.get("maintain_counts")
        if isinstance(target.get("maintain_counts"), dict)
        else {}
    )
    s_counts = (
        source.get("maintain_counts")
        if isinstance(source.get("maintain_counts"), dict)
        else {}
    )
    for key in ("merged", "archived", "consolidated", "unchanged"):
        t_counts[key] = int(t_counts.get(key) or 0) + int(s_counts.get(key) or 0)
    target["maintain_counts"] = t_counts


def _aggregate_sync_totals(
    projects_metrics: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Build sync totals across projects for details_json."""
    totals = {
        "sessions_analyzed": 0,
        "sessions_extracted": 0,
        "sessions_failed": 0,
        "sessions_skipped": 0,
        "records_created": 0,
        "records_updated": 0,
        "records_archived": 0,
        "projects_count": len(projects_metrics),
    }
    for metrics in projects_metrics.values():
        for key in (
            "sessions_analyzed",
            "sessions_extracted",
            "sessions_failed",
            "sessions_skipped",
            "records_created",
            "records_updated",
            "records_archived",
        ):
            totals[key] += int(metrics.get(key) or 0)
    return totals


def _aggregate_maintain_totals(
    projects_metrics: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Build maintain totals across projects for details_json."""
    counts = {"merged": 0, "archived": 0, "consolidated": 0, "unchanged": 0}
    totals = {
        "projects_count": len(projects_metrics),
        "records_created": 0,
        "records_updated": 0,
        "records_archived": 0,
        "counts": counts,
    }
    for metrics in projects_metrics.values():
        for key in ("records_created", "records_updated", "records_archived"):
            totals[key] += int(metrics.get(key) or 0)
        m_counts = (
            metrics.get("maintain_counts")
            if isinstance(metrics.get("maintain_counts"), dict)
            else {}
        )
        for key in ("merged", "archived", "consolidated", "unchanged"):
            counts[key] += int(m_counts.get(key) or 0)
    return totals


def _process_one_job(job: dict[str, Any]) -> dict[str, Any]:
    """Process a single claimed session job. Thread-safe (own agent instance)."""
    started_monotonic = time.monotonic()
    rid = str(job.get("run_id") or "")
    if not rid:
        return {
            "status": "skipped",
            "run_id": rid,
            "project_name": "unknown",
            "repo_path": "",
            "metrics": {},
            "duration_ms": int((time.monotonic() - started_monotonic) * 1000),
        }

    repo_path = str(job.get("repo_path") or "").strip()
    project_name = Path(repo_path).name if repo_path else "unknown"

    # Skip sessions that don't match a registered project
    if not repo_path:
        complete_session_job(rid)
        return {
            "status": "skipped",
            "reason": "no_project_match",
            "run_id": rid,
            "project_name": project_name,
            "repo_path": repo_path,
            "metrics": {},
            "duration_ms": int((time.monotonic() - started_monotonic) * 1000),
        }

    attempts = max(int(job.get("attempts") or 1), 1)
    try:
        doc = fetch_session_doc(rid) or {}
        session_path = str(job.get("session_path") or "").strip()
        if not session_path:
            session_path = str(doc.get("session_path") or "").strip()
        if not session_path:
            error = "Session job is missing session_path; cannot extract."
            fail_session_job(
                rid,
                error=error,
                retry_backoff_seconds=_retry_backoff_seconds(attempts),
            )
            return {
                "status": "failed",
                "run_id": rid,
                "project_name": project_name,
                "repo_path": repo_path,
                "error": error,
                "metrics": {},
                "duration_ms": int((time.monotonic() - started_monotonic) * 1000),
            }
        agent = LerimRuntime(default_cwd=repo_path)
        heartbeat_stop = _start_job_heartbeat(rid)
        try:
            result = agent.sync(
                Path(session_path),
                session_id=rid,
                agent_type=str(
                    job.get("agent_type") or doc.get("agent_type") or "unknown"
                ),
                session_meta={
                    "cwd": str(job.get("repo_path") or repo_path),
                    "started_at": str(
                        job.get("start_time") or doc.get("start_time") or ""
                    ),
                },
            )
        finally:
            heartbeat_stop.set()
    except Exception as exc:
        fail_session_job(
            rid,
            error=str(exc),
            retry_backoff_seconds=_retry_backoff_seconds(attempts),
        )
        return {
            "status": "failed",
            "run_id": rid,
            "project_name": project_name,
            "repo_path": repo_path,
            "error": str(exc),
            "metrics": {},
            "duration_ms": int((time.monotonic() - started_monotonic) * 1000),
        }
    complete_session_job(rid)
    artifact_run_id = str(
        result.get("mlflow_client_request_id")
        or Path(str(result.get("run_folder") or "")).name
        or ""
    )
    return {
        "status": "extracted",
        "run_id": rid,
        "artifact_run_id": artifact_run_id,
        "mlflow_client_request_id": str(
            result.get("mlflow_client_request_id") or artifact_run_id
        ),
        "run_folder": str(result.get("run_folder") or ""),
        "artifacts": result.get("artifacts")
        if isinstance(result.get("artifacts"), dict)
        else {},
        "project_name": project_name,
        "repo_path": repo_path,
        "cost_usd": float(result.get("cost_usd") or 0),
        "metrics": {
            "records_created": int(result.get("records_created") or 0),
            "records_updated": int(result.get("records_updated") or 0),
            "records_archived": int(result.get("records_archived") or 0),
        },
        "duration_ms": int((time.monotonic() - started_monotonic) * 1000),
    }


def _process_claimed_jobs(
    claimed: list[dict[str, Any]],
) -> tuple[int, int, int, float, dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """Process claimed jobs sequentially in the queue-selected order.

    Normal sync claims newest-first to improve first-run backlog quality.  A
    chronological replay caller can still ask the queue for oldest-first jobs.

    Returns
        (extracted, failed, skipped, cost_usd, projects_metrics, events).
    """
    extracted = 0
    failed = 0
    skipped = 0
    cost_usd = 0.0
    projects_metrics: dict[str, dict[str, Any]] = {}
    events: list[dict[str, Any]] = []
    for job in claimed:
        result = _process_one_job(job)
        project_name = str(result.get("project_name") or "unknown")
        metric_row = projects_metrics.setdefault(project_name, _new_project_metric())
        metric_row["sessions_analyzed"] = (
            int(metric_row.get("sessions_analyzed") or 0) + 1
        )
        metric_row["duration_ms"] = int(metric_row.get("duration_ms") or 0) + int(
            result.get("duration_ms") or 0
        )
        delta = result.get("metrics") if isinstance(result.get("metrics"), dict) else {}
        for key in ("records_created", "records_updated", "records_archived"):
            metric_row[key] = int(metric_row.get(key) or 0) + int(delta.get(key) or 0)

        event = {
            "time": _now_iso(),
            "op_type": "sync",
            "status": str(result.get("status") or "unknown"),
            "project": project_name,
            "run_id": str(result.get("run_id") or ""),
            "sessions_analyzed": 1,
            "sessions_extracted": 0,
            "sessions_failed": 0,
            "sessions_skipped": 0,
            "records_created": int(delta.get("records_created") or 0),
            "records_updated": int(delta.get("records_updated") or 0),
            "records_archived": int(delta.get("records_archived") or 0),
            "duration_ms": int(result.get("duration_ms") or 0),
        }
        for key in (
            "artifact_run_id",
            "mlflow_client_request_id",
            "run_folder",
            "artifacts",
        ):
            if result.get(key):
                event[key] = result[key]

        if result["status"] == "extracted":
            extracted += 1
            metric_row["sessions_extracted"] = (
                int(metric_row.get("sessions_extracted") or 0) + 1
            )
            event["sessions_extracted"] = 1
            cost_usd += result.get("cost_usd", 0.0)
        elif result["status"] == "failed":
            failed += 1
            metric_row["sessions_failed"] = (
                int(metric_row.get("sessions_failed") or 0) + 1
            )
            metric_row["last_error"] = str(result.get("error") or "")
            event["sessions_failed"] = 1
            if result.get("error"):
                event["error"] = str(result.get("error"))
        elif result["status"] == "skipped":
            skipped += 1
            metric_row["sessions_skipped"] = (
                int(metric_row.get("sessions_skipped") or 0) + 1
            )
            event["sessions_skipped"] = 1
            if result.get("reason"):
                event["reason"] = str(result.get("reason"))
        events.append(event)
    return extracted, failed, skipped, cost_usd, projects_metrics, events


def run_sync_once(
    *,
    run_id: str | None,
    agent_filter: list[str] | None,
    no_extract: bool,
    force: bool,
    max_sessions: int,
    dry_run: bool,
    ignore_lock: bool,
    trigger: str,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
) -> tuple[int, SyncSummary]:
    """Run one sync cycle: index sessions, enqueue jobs, process extraction."""
    t0 = time.monotonic()
    reload_config()

    started = _now_iso()
    status = "completed"
    lock = None
    if not dry_run and not ignore_lock:
        lock = ServiceLock(lock_path(WRITER_LOCK_NAME), stale_seconds=60)
        try:
            lock.acquire("sync", "lerim sync")
        except LockBusyError as exc:
            op_result = OperationResult(
                operation="sync",
                status="lock_busy",
                trigger=trigger,
                error=str(exc),
            )
            _record_service_event(
                record_service_run,
                job_type="sync",
                status="lock_busy",
                started_at=started,
                trigger=trigger,
                details=op_result.to_details_json(),
            )
            return EXIT_LOCK_BUSY, _empty_sync_summary()

    try:
        record_service_run(
            job_type="sync",
            status="started",
            started_at=started,
            completed_at=None,
            trigger=trigger,
            details=None,
        )

        config = get_config()
        target_run_ids: list[str] = []
        indexed_sessions = 0
        queued_sessions = 0
        skipped_unscoped = 0
        if run_id:
            target_run_ids = [run_id]
            if not dry_run:
                session = fetch_session_doc(run_id)
                session_repo_path = (
                    str(session.get("repo_path") or "") if session else ""
                )
                match = match_session_project(
                    session_repo_path or None, config.projects
                )
                matched_path = str(match[1]) if match else None
                if not matched_path:
                    skipped_unscoped = 1
                else:
                    queued = enqueue_session_job(
                        run_id,
                        agent_type=session.get("agent_type") if session else None,
                        session_path=session.get("session_path") if session else None,
                        start_time=session.get("start_time") if session else None,
                        trigger=trigger,
                        force=True,
                        repo_path=matched_path,
                    )
                    queued_sessions = 1 if queued else 0
        else:
            if dry_run:
                target_run_ids = []
            else:
                index_stats: dict[str, int] = {"skipped_unscoped": 0}
                indexed = index_new_sessions(
                    agents=agent_filter,
                    return_details=True,
                    start=window_start,
                    end=window_end,
                    projects=config.projects,
                    skip_unscoped=True,
                    stats=index_stats,
                )
                details: list[IndexedSession] = (
                    indexed if isinstance(indexed, list) else []
                )
                indexed_sessions = len(details)
                skipped_unscoped = int(index_stats.get("skipped_unscoped") or 0)
                for item in details:
                    match = match_session_project(item.repo_path, config.projects)
                    if match is None:
                        continue
                    _project_name, project_path = match
                    queued = enqueue_session_job(
                        item.run_id,
                        agent_type=item.agent_type,
                        session_path=item.session_path,
                        start_time=item.start_time,
                        trigger=trigger,
                        force=force or item.changed,
                        repo_path=str(project_path),
                    )
                    if queued:
                        queued_sessions += 1
                target_run_ids = [item.run_id for item in details]

        extracted = 0
        skipped = 0
        failed = 0
        cost_usd = 0.0
        projects: set[str] = set()
        projects_metrics: dict[str, dict[str, Any]] = {}
        sync_events: list[dict[str, Any]] = []
        claim_limit = max(max_sessions, 1)

        if no_extract:
            skipped = len(target_run_ids)
        elif not dry_run:
            # Process up to max_sessions by claiming in a loop.
            # Each claim returns at most 1 job per project.  Normal backlog
            # extraction is newest-first; explicit replay can request
            # chronological order directly from the catalog API.
            # After processing, claim again to get the next session.
            total_processed = 0
            while total_processed < claim_limit:
                reap_stale_running_jobs(
                    lease_seconds=RUNNING_JOB_LEASE_SECONDS,
                    retry_backoff_fn=_retry_backoff_seconds,
                )
                claimed = claim_session_jobs(
                    limit=claim_limit - total_processed,
                    run_ids=[run_id] if run_id else None,
                    claim_order="newest",
                )
                if not claimed:
                    break  # no more pending jobs
                for job in claimed:
                    rp = str(job.get("repo_path") or "").strip()
                    if rp:
                        projects.add(Path(rp).name)
                for item in claimed:
                    claimed_run_id = str(item.get("run_id") or "")
                    if claimed_run_id and claimed_run_id not in target_run_ids:
                        target_run_ids.append(claimed_run_id)
                (
                    batch_extracted,
                    batch_failed,
                    batch_skipped,
                    batch_cost,
                    batch_projects_metrics,
                    batch_events,
                ) = _process_claimed_jobs(claimed)
                extracted += batch_extracted
                failed += batch_failed
                skipped += batch_skipped
                cost_usd += batch_cost
                for project_name, metrics in batch_projects_metrics.items():
                    target = projects_metrics.setdefault(
                        project_name, _new_project_metric()
                    )
                    _merge_project_metric(target, metrics)
                sync_events.extend(batch_events)
                total_processed += len(claimed)

        summary = SyncSummary(
            indexed_sessions=indexed_sessions,
            extracted_sessions=extracted,
            skipped_sessions=skipped,
            skipped_unscoped=skipped_unscoped,
            failed_sessions=failed,
            run_ids=target_run_ids,
            cost_usd=cost_usd,
        )

        code = EXIT_OK
        if failed > 0 and extracted > 0:
            code = EXIT_PARTIAL
            status = "partial"
        elif failed > 0 and extracted == 0:
            code = EXIT_FATAL
            status = "failed"

        sync_totals = _aggregate_sync_totals(projects_metrics)
        sync_totals["indexed_sessions"] = indexed_sessions
        sync_totals["queued_sessions"] = queued_sessions
        sync_totals["skipped_unscoped"] = skipped_unscoped

        op_result = OperationResult(
            operation="sync",
            status=status
            if code == EXIT_OK
            else ("partial" if code == EXIT_PARTIAL else "failed"),
            trigger=trigger,
            indexed_sessions=indexed_sessions,
            queued_sessions=queued_sessions,
            extracted_sessions=extracted,
            skipped_sessions=skipped,
            skipped_unscoped=skipped_unscoped,
            failed_sessions=failed,
            run_ids=target_run_ids,
            window_start=window_start.isoformat() if window_start else None,
            window_end=window_end.isoformat() if window_end else None,
            dry_run=dry_run,
            cost_usd=cost_usd,
            sync_metrics=sync_totals,
            projects_metrics=projects_metrics,
            events=sync_events[-200:],
        )
        _record_service_event(
            record_service_run,
            job_type="sync",
            status=op_result.status,
            started_at=started,
            trigger=trigger,
            details=op_result.to_details_json(),
        )
        if not dry_run and extracted:
            log_activity(
                "sync",
                ", ".join(sorted(projects)) or "global",
                f"{extracted} sessions",
                time.monotonic() - t0,
                cost_usd=cost_usd,
            )
        return code, summary
    except Exception as exc:
        op_result = OperationResult(
            operation="sync",
            status="failed",
            trigger=trigger,
            error=str(exc),
        )
        _record_service_event(
            record_service_run,
            job_type="sync",
            status="failed",
            started_at=started,
            trigger=trigger,
            details=op_result.to_details_json(),
        )
        return EXIT_FATAL, _empty_sync_summary()
    finally:
        if lock:
            lock.release()


def run_maintain_once(
    *,
    dry_run: bool,
    trigger: str = "manual",
) -> tuple[int, dict]:
    """Run one maintain cycle with lock handling and service run record."""
    t0 = time.monotonic()
    reload_config()

    started = _now_iso()

    if dry_run:
        op_result = OperationResult(
            operation="maintain",
            status="completed",
            trigger=trigger,
            dry_run=True,
        )
        _record_service_event(
            record_service_run,
            job_type="maintain",
            status="completed",
            started_at=started,
            trigger=trigger,
            details=op_result.to_details_json(),
        )
        return EXIT_OK, {"dry_run": True}

    writer = ServiceLock(lock_path(WRITER_LOCK_NAME), stale_seconds=60)
    try:
        writer.acquire("maintain", "lerim maintain")
    except LockBusyError as exc:
        op_result = OperationResult(
            operation="maintain",
            status="lock_busy",
            trigger=trigger,
            error=str(exc),
        )
        _record_service_event(
            record_service_run,
            job_type="maintain",
            status="lock_busy",
            started_at=started,
            trigger=trigger,
            details=op_result.to_details_json(),
        )
        return EXIT_LOCK_BUSY, {"error": str(exc)}

    try:
        config = get_config()
        projects = config.projects or {}
        if not projects:
            op_result = OperationResult(
                operation="maintain",
                status="completed",
                trigger=trigger,
                projects={},
            )
            details = op_result.to_details_json()
            details["message"] = (
                "No registered projects. Add one with `lerim project add <path>`."
            )
            _record_service_event(
                record_service_run,
                job_type="maintain",
                status="completed",
                started_at=started,
                trigger=trigger,
                details=details,
            )
            return EXIT_OK, details

        results: dict[str, dict] = {}
        projects_metrics: dict[str, dict[str, Any]] = {}
        maintain_events: list[dict[str, Any]] = []
        failed_projects: list[str] = []
        for project_name, project_path_str in projects.items():
            project_path = Path(project_path_str).expanduser().resolve()
            started_project = time.monotonic()
            metric_row = _new_project_metric()
            try:
                agent = LerimRuntime(default_cwd=str(project_path))
                result = agent.maintain(repo_root=project_path)
                results[project_name] = result
                maintain_cost = float(result.get("cost_usd") or 0)
                if maintain_cost:
                    log_activity(
                        "maintain",
                        project_name,
                        "maintenance completed",
                        time.monotonic() - t0,
                        cost_usd=maintain_cost,
                    )
                metric_row["records_created"] = int(result.get("records_created") or 0)
                metric_row["records_updated"] = int(result.get("records_updated") or 0)
                metric_row["records_archived"] = int(
                    result.get("records_archived") or 0
                )
                metric_row["duration_ms"] = int(
                    (time.monotonic() - started_project) * 1000
                )
                raw_counts = (
                    result.get("counts")
                    if isinstance(result.get("counts"), dict)
                    else {}
                )
                metric_row["maintain_counts"] = {
                    "merged": int(raw_counts.get("merged") or 0),
                    "archived": int(raw_counts.get("archived") or 0),
                    "consolidated": int(raw_counts.get("consolidated") or 0),
                    "unchanged": int(raw_counts.get("unchanged") or 0),
                }
            except Exception as exc:
                failed_projects.append(project_name)
                results[project_name] = {"error": str(exc)}
                metric_row["last_error"] = str(exc)
                metric_row["duration_ms"] = int(
                    (time.monotonic() - started_project) * 1000
                )
            projects_metrics[project_name] = metric_row
            maintain_events.append(
                {
                    "time": _now_iso(),
                    "op_type": "maintain",
                    "status": "failed" if metric_row.get("last_error") else "completed",
                    "project": project_name,
                    "records_created": int(metric_row.get("records_created") or 0),
                    "records_updated": int(metric_row.get("records_updated") or 0),
                    "records_archived": int(metric_row.get("records_archived") or 0),
                    "duration_ms": int(metric_row.get("duration_ms") or 0),
                    "maintain_counts": metric_row.get("maintain_counts") or {},
                    "error": str(metric_row.get("last_error") or ""),
                }
            )

        status = (
            "failed"
            if failed_projects and not (set(projects) - set(failed_projects))
            else ("partial" if failed_projects else "completed")
        )
        maintain_totals = _aggregate_maintain_totals(projects_metrics)
        op_result = OperationResult(
            operation="maintain",
            status=status,
            trigger=trigger,
            projects=results,
            maintain_metrics=maintain_totals,
            projects_metrics=projects_metrics,
            events=maintain_events[-200:],
        )
        _record_service_event(
            record_service_run,
            job_type="maintain",
            status=status,
            started_at=started,
            trigger=trigger,
            details=op_result.to_details_json(),
        )
        code = (
            EXIT_FATAL
            if status == "failed"
            else (EXIT_PARTIAL if status == "partial" else EXIT_OK)
        )
        return code, op_result.to_response_json()
    except Exception as exc:
        op_result = OperationResult(
            operation="maintain",
            status="failed",
            trigger=trigger,
            error=str(exc),
        )
        _record_service_event(
            record_service_run,
            job_type="maintain",
            status="failed",
            started_at=started,
            trigger=trigger,
            details=op_result.to_details_json(),
        )
        return EXIT_FATAL, {"error": str(exc)}
    finally:
        writer.release()
