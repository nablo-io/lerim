"""Shared terminal renderer for `lerim status` snapshot and `--live` modes."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text


def _format_queue_counts(counts: dict[str, int]) -> str:
    """Format queue status counts into a compact status string."""
    order = ["pending", "running", "done", "failed", "dead_letter"]
    parts: list[str] = []
    for status in order:
        n = int(counts.get(status, 0))
        if n > 0:
            parts.append(f"{n} {status}")
    return ", ".join(parts) if parts else "empty"


def _project_state(project: dict[str, Any]) -> str:
    """Compute high-level stream state for one project row."""
    queue = project.get("queue") or {}
    dead = int(queue.get("dead_letter") or 0)
    running = int(queue.get("running") or 0)
    pending = int(queue.get("pending") or 0)
    record_count = int(project.get("record_count") or 0)
    indexed_sessions = int(project.get("indexed_sessions_count") or 0)
    if dead > 0 and str(project.get("oldest_blocked_run_id") or "").strip():
        return "blocked"
    if running > 0:
        return "running"
    if pending > 0:
        return "queued"
    if record_count > 0 or indexed_sessions > 0 or int(queue.get("done") or 0) > 0:
        return "quiet"
    return "idle"


def _project_next_action(project: dict[str, Any]) -> str:
    """Return concise action guidance for one project stream."""
    name = str(project.get("name") or "").strip()
    queue = project.get("queue") or {}
    dead = int(queue.get("dead_letter") or 0)
    if dead > 0:
        blocked = str(project.get("oldest_blocked_run_id") or "").strip()
        if blocked:
            return f"lerim retry {blocked} / lerim skip {blocked}"
        return f"lerim retry --project {name}"
    if int(queue.get("running") or 0) > 0:
        return f"lerim queue --project {name}"
    if int(queue.get("pending") or 0) > 0:
        return f"lerim queue --project {name}"
    return "-"


def _parse_iso(raw: str | None) -> datetime | None:
    """Parse ISO timestamp safely."""
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None


def _format_minutes(minutes: Any) -> str:
    """Format an interval in minutes for status output."""
    try:
        value = int(minutes)
    except (TypeError, ValueError):
        return "unknown"
    if value < 60:
        return f"{value}m"
    hours, rem = divmod(value, 60)
    if rem == 0:
        return f"{hours}h"
    return f"{hours}h {rem}m"


def _format_countdown(seconds: Any) -> str:
    """Format seconds until a scheduled task is due."""
    if seconds is None:
        return "unknown"
    try:
        value = max(0, int(seconds))
    except (TypeError, ValueError):
        return "unknown"
    if value == 0:
        return "due now"
    if value < 60:
        return f"in {value}s"
    minutes, sec = divmod(value, 60)
    if minutes < 60:
        return f"in {minutes}m {sec}s" if sec else f"in {minutes}m"
    hours, minutes = divmod(minutes, 60)
    return f"in {hours}h {minutes}m" if minutes else f"in {hours}h"


def _format_schedule_item(item: dict[str, Any]) -> str:
    """Render one daemon schedule row."""
    interval = _format_minutes(item.get("interval_minutes"))
    if item.get("running"):
        return f"every {interval}; running now"
    countdown = _format_countdown(item.get("seconds_until_next"))
    due = _parse_iso(str(item.get("next_due_at") or ""))
    if due:
        due_text = due.astimezone(timezone.utc).strftime("%H:%M:%SZ")
        return f"every {interval}; next {countdown} ({due_text})"
    return f"every {interval}; next {countdown}"


def _render_activity_line(item: dict[str, Any]) -> str:
    """Render one compact activity line for sync or maintain."""
    when = _parse_iso(str(item.get("time") or item.get("started_at") or ""))
    when_txt = (
        when.astimezone(timezone.utc).strftime("%H:%M:%SZ") if when else "unknown-time"
    )
    op = str(item.get("op_type") or item.get("type") or "sync").strip().lower()
    status = str(item.get("status") or "unknown").strip().lower()
    project = str(item.get("project_label") or "global").strip()
    err = str(item.get("error") or "").strip()
    if len(err) > 72:
        err = f"{err[:69]}..."

    if op == "maintain":
        counts = item.get("maintain_counts") or {}
        merged = int(counts.get("merged") or 0)
        archived = int(counts.get("archived") or 0)
        consolidated = int(counts.get("consolidated") or 0)
        unchanged = int(counts.get("unchanged") or 0)
        new = int(item.get("records_created") or 0)
        upd = int(item.get("records_updated") or 0)
        arc = int(item.get("records_archived") or 0)
        base = (
            f"{when_txt} {project} | maintain/{status} | "
            f"merged {merged}, archived {archived}, consolidated {consolidated}, unchanged {unchanged} | "
            f"+{new} ~{upd} -{arc}"
        )
    else:
        analyzed = int(item.get("sessions_analyzed") or 0)
        extracted = int(item.get("sessions_extracted") or 0)
        failed = int(item.get("sessions_failed") or 0)
        new = int(item.get("records_created") or 0)
        upd = int(item.get("records_updated") or 0)
        arc = int(item.get("records_archived") or 0)
        base = (
            f"{when_txt} {project} | sync/{status} | "
            f"{analyzed} analyzed, {extracted} extracted, {failed} failed | "
            f"+{new} ~{upd} -{arc}"
        )
    if err:
        return f"{base} | error: {err}"
    return base


def render_status_output(payload: dict[str, Any], *, refreshed_at: str) -> Group:
    """Build shared rich output for both status snapshot and `--live`."""
    queue = payload.get("queue") or {}
    scope_data = payload.get("scope") or {}
    projects = payload.get("projects") or []
    unscoped = payload.get("unscoped_sessions") or {}
    queue_health = payload.get("queue_health") or {}
    recent_activity = payload.get("recent_activity") or []
    schedule = payload.get("schedule") or {}
    runtime = payload.get("runtime") or {}
    blocked_projects = [p for p in projects if _project_state(p) == "blocked"]

    summary = Table.grid(expand=True, padding=(0, 2))
    summary.add_column(justify="left", style="bold")
    summary.add_column(justify="left")
    summary.add_row("Connected agents", str(len(payload.get("connected_agents", []))))
    runtime_source = str(runtime.get("source") or "unknown")
    runtime_image = str(runtime.get("image") or "")
    runtime_text = (
        f"{runtime_source} ({runtime_image})" if runtime_image else runtime_source
    )
    summary.add_row("Runtime", runtime_text)
    summary.add_row("Context records", str(int(payload.get("record_count") or 0)))
    summary.add_row(
        "Indexed sessions", str(int(payload.get("sessions_indexed_count") or 0))
    )
    sync_window_days = payload.get("sync_window_days")
    sync_window_text = (
        f"last {int(sync_window_days)}d (discovery + queueing)"
        if sync_window_days is not None
        else "unknown (restart daemon after upgrade)"
    )
    summary.add_row(
        "Sync window",
        sync_window_text,
    )
    summary.add_row("Sync interval", _format_schedule_item(schedule.get("sync") or {}))
    summary.add_row(
        "Maintain interval",
        _format_schedule_item(schedule.get("maintain") or {}),
    )
    summary.add_row("Queue", _format_queue_counts(queue))
    summary.add_row(
        "Unscoped sessions",
        f"{int(unscoped.get('total') or 0)} ({json.dumps(unscoped.get('by_agent') or {}, ensure_ascii=True)})",
    )
    summary.add_row(
        "Skipped unscoped (last sync)",
        str(int(scope_data.get("skipped_unscoped") or 0)),
    )
    summary.add_row(
        "Streams",
        "Independent per project (one blocked stream does not stop others)",
    )

    project_table = Table(title="Project Streams", expand=True)
    project_table.add_column("Project", no_wrap=True)
    project_table.add_column("State", no_wrap=True)
    project_table.add_column("Queue")
    project_table.add_column("Records", no_wrap=True)
    project_table.add_column("Blocker", no_wrap=True)
    project_table.add_column("Next Step")

    state_style = {
        "blocked": "red bold",
        "running": "cyan",
        "queued": "yellow",
        "quiet": "green",
        "idle": "dim",
    }

    for item in projects:
        name = str(item.get("name") or "")
        state = _project_state(item)
        style = state_style.get(state, "white")
        pqueue = item.get("queue") or {}
        queue_text = _format_queue_counts(pqueue)
        record_count = str(int(item.get("record_count") or 0))
        blocked_by = str(item.get("oldest_blocked_run_id") or "").strip()
        blocked_short = blocked_by[:16] if blocked_by else "-"
        project_table.add_row(
            name,
            f"[{style}]{state}[/{style}]",
            queue_text,
            record_count,
            blocked_short,
            _project_next_action(item),
        )

    meaning = Table.grid(expand=True, padding=(0, 2))
    meaning.add_column(justify="left", style="bold")
    meaning.add_column(justify="left")
    meaning.add_row(
        "project stream", "Queue + extraction flow for one registered project."
    )
    meaning.add_row(
        "blocked", "Oldest job is dead_letter; this project stream is paused."
    )
    meaning.add_row("running", "A job is being processed now.")
    meaning.add_row("queued", "Jobs are waiting; stream is not blocked.")
    meaning.add_row("quiet", "Past in-scope sessions exist; no queued work right now.")
    meaning.add_row(
        "idle", "No indexed sessions in the current sync window for this project."
    )
    meaning.add_row(
        "unscoped", "Indexed sessions with no registered project match (not extracted)."
    )
    meaning.add_row("dead_letter", "Failed max retries; needs retry or skip.")

    action_lines: list[str] = []
    if blocked_projects:
        action_lines.append(
            f"{len(blocked_projects)} project(s) blocked. Other projects continue independently."
        )
        action_lines.append("Inspect blockers: lerim queue --failed")
        for item in blocked_projects[:3]:
            name = str(item.get("name") or "")
            blocked = str(item.get("oldest_blocked_run_id") or "").strip()
            if blocked:
                action_lines.append(f"Unblock {name}: lerim retry {blocked}")
            else:
                action_lines.append(f"Unblock {name}: lerim retry --project {name}")
    else:
        action_lines.append("No blocked projects.")

    if queue_health.get("degraded"):
        advice = str(queue_health.get("advice") or "").strip()
        if advice:
            action_lines.append(f"Queue health: {advice}")

    activity_lines = [
        _render_activity_line(item)
        for item in recent_activity[:8]
        if isinstance(item, dict)
    ]
    if not activity_lines:
        activity_lines = ["No recent sync/maintain activity yet."]

    header = Text(f"Lerim Status ({refreshed_at})", style="bold blue")
    blocked_table: Table | None = None
    if blocked_projects:
        blocked_table = Table(title="Blocked Streams", expand=True)
        blocked_table.add_column("Project", no_wrap=True)
        blocked_table.add_column("Run ID", no_wrap=True)
        blocked_table.add_column("Reason")
        blocked_table.add_column("Fix", no_wrap=True)
        for item in blocked_projects:
            name = str(item.get("name") or "").strip()
            run_id = str(item.get("oldest_blocked_run_id") or "").strip()
            reason = (
                str(item.get("last_error") or "").strip() or "dead_letter at queue head"
            )
            if len(reason) > 120:
                reason = f"{reason[:117]}..."
            fix = f"lerim retry {run_id}" if run_id else f"lerim retry --project {name}"
            blocked_table.add_row(name, run_id or "-", reason, fix)

    parts: list[Any] = [header, Panel(summary, title="Summary", border_style="blue")]
    if projects:
        parts.append(project_table)
    if blocked_table is not None:
        parts.append(blocked_table)
    parts.append(
        Panel(
            "\n".join(activity_lines),
            title="Activity (Sync + Maintain)",
            border_style="cyan",
        )
    )
    parts.append(Panel(meaning, title="What These Terms Mean", border_style="magenta"))
    parts.append(
        Panel("\n".join(action_lines), title="What To Do Next", border_style="green")
    )
    return Group(*parts)
