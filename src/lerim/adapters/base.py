"""Shared adapter data models and protocol contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol


@dataclass
class ViewerMessage:
    """Normalized message shape for dashboard/session viewers."""

    role: str
    content: str | None = None
    timestamp: str | None = None
    model: str | None = None
    tool_name: str | None = None
    tool_input: Any | None = None
    tool_output: Any | None = None
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class ViewerSession:
    """Normalized session payload returned by platform adapters."""

    session_id: str
    cwd: str | None = None
    git_branch: str | None = None
    messages: list[ViewerMessage] = field(default_factory=list)
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    meta: dict[str, Any] = field(default_factory=dict)


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


class Adapter(Protocol):
    """Platform adapter protocol for discovering and loading sessions."""

    def default_path(self) -> Path | None:
        """Return the default traces directory for this platform."""

    def count_sessions(self, path: Path) -> int:
        """Return total session count under ``path``."""

    def iter_sessions(
        self,
        traces_dir: Path | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        known_run_ids: set[str] | None = None,
    ) -> list[SessionRecord]:
        """List normalized session summaries in the selected time window."""


if __name__ == "__main__":
    session = ViewerSession(
        session_id="demo",
        messages=[ViewerMessage(role="assistant", content="ok")],
    )
    record = SessionRecord(
        run_id="demo", agent_type="codex", session_path="/tmp/demo.json"
    )
    assert session.messages and session.messages[0].content == "ok"
    assert record.run_id == "demo"
