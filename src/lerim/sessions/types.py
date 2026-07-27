"""Shared session data models used by the catalog and its trace sources."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SessionRecord:
    """Summary record used for indexing and session listings."""

    run_id: str
    agent_type: str
    session_path: str
    start_time: str | None = None
    repo_path: str | None = None
    repo_name: str | None = None
    status: str = "completed"
    duration_ms: int = 0
    message_count: int = 0
    tool_call_count: int = 0
    error_count: int = 0
    total_tokens: int = 0
    summaries: list[str] = field(default_factory=list)
    content_hash: str | None = None


if __name__ == "__main__":
    """Run a real-path smoke test for the session record model."""
    record = SessionRecord(
        run_id="demo", agent_type="claude", session_path="/tmp/demo.jsonl"
    )
    assert record.run_id == "demo"
    assert record.status == "completed"
    assert record.summaries == []
