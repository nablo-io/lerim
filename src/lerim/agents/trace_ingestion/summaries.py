"""Rolling summaries for trace ingestion prompts."""

from __future__ import annotations

from typing import Any

MAX_EPISODE_SUMMARY_ITEMS = 30
MAX_IMPLEMENTATION_SUMMARY_ITEMS = 48
MAX_DISCARDED_NOISE_SUMMARY_ITEMS = 32

def _window_line_refs(window: dict[str, Any]) -> list[str]:
    """Return one line reference for each line in a trace window."""
    start_line = int(window.get("start_line") or 0)
    end_line = int(window.get("end_line") or 0)
    if start_line <= 0 or end_line < start_line:
        return []
    return [f"line:{line}" for line in range(start_line, end_line + 1)]


def _episode_summary(state: dict[str, Any]) -> str:
    """Render compact rolling episode summary."""
    updates = [item for item in state.get("episode_updates", []) if item]
    updates, omitted = _head_and_tail(updates, MAX_EPISODE_SUMMARY_ITEMS)
    lines = [f"- {item}" for item in updates]
    if omitted:
        lines.insert(-1, f"- ... [{omitted} middle episode updates omitted]")
    return "\n".join(lines) or "(none yet)"


def _synthesis_episode_summary(state: dict[str, Any]) -> str:
    """Render a final episode summary without carrying discarded details forward."""
    if state.get("filtered_durable_findings"):
        return _episode_summary(state)
    if state.get("implementation_findings") or state.get("discarded_noise"):
        return (
            "No reusable durable context was found; source-session details were "
            "implementation evidence or discarded noise."
        )
    return _episode_summary(state)


def _findings_summary(state: dict[str, Any]) -> str:
    """Render all prior findings for the next scan window."""
    return "\n\n".join(
        [
            "Durable findings:\n" + _durable_findings_summary(state),
            "Implementation/noise findings:\n" + _implementation_summary(state),
        ]
    )


def _durable_findings_summary(state: dict[str, Any]) -> str:
    """Render durable findings compactly for model prompts."""
    findings = state.get("durable_findings", [])
    if not findings:
        return "(none)"
    return "\n".join(_format_finding(finding) for finding in findings)


def _filtered_durable_findings_summary(state: dict[str, Any]) -> str:
    """Render filtered durable findings compactly for final synthesis."""
    findings = state.get("filtered_durable_findings") or []
    if not findings:
        return "(none)"
    summary = str(state.get("signal_filter_summary") or "").strip()
    rendered = "\n".join(_format_finding(finding) for finding in findings)
    if summary:
        return f"Filter summary: {summary}\n{rendered}"
    return rendered


def _rejected_durable_findings_summary(state: dict[str, Any]) -> str:
    """Render rejected durable candidates for final high-priority restoration."""
    findings = state.get("rejected_durable_findings") or []
    if not findings:
        return "(none)"
    return "\n".join(_format_finding(finding) for finding in findings)


def _implementation_summary(state: dict[str, Any]) -> str:
    """Render implementation findings and discarded noise compactly."""
    parts: list[str] = []
    findings = state.get("implementation_findings", [])
    if findings:
        selected, omitted = _head_and_tail(findings, MAX_IMPLEMENTATION_SUMMARY_ITEMS)
        lines = [_format_finding(finding) for finding in selected]
        if omitted:
            lines.insert(
                -1,
                f"- ... [{omitted} middle implementation/noise findings omitted]",
            )
        parts.append("\n".join(lines))
    noise = state.get("discarded_noise", [])
    if noise:
        selected, omitted = _head_and_tail(noise, MAX_DISCARDED_NOISE_SUMMARY_ITEMS)
        lines = [f"- {item}" for item in selected]
        if omitted:
            lines.insert(-1, f"- ... [{omitted} middle noise categories omitted]")
        parts.append("Discarded noise:\n" + "\n".join(lines))
    return "\n".join(parts) if parts else "(none)"


def _head_and_tail(items: list[Any], limit: int) -> tuple[list[Any], int]:
    """Keep early intent plus recent context while bounding long summaries."""
    if len(items) <= limit:
        return items, 0
    head_count = max(1, limit // 4)
    tail_count = max(1, limit - head_count)
    return [*items[:head_count], *items[-tail_count:]], len(items) - limit


def _format_finding(finding: dict[str, Any]) -> str:
    """Render one scan finding as one compact bullet."""
    kind = str(finding.get("kind") or "").strip()
    theme = str(finding.get("theme") or "").strip()
    note = str(finding.get("note") or "").strip()
    line = finding.get("line")
    quote = str(finding.get("quote") or "").strip()
    prefix = f"- {kind}: {theme}" if kind or theme else "-"
    details = note
    if line:
        details += f" (line:{line})"
    if quote:
        details += f" Evidence: {quote}"
    return f"{prefix}: {details}".strip()
