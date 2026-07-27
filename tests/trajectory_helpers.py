"""Builders for real harness transcripts, shared by adapter and catalog tests.

Tests that exercise discovery, normalization, or ingest run the real node
normalizer over real files rather than faking a session source, because the
record shape, the tool-call linkage and the turn count are exactly what those
tests are about. This module writes the raw transcripts those tests need.

The claude-code layout is the one Lerim reads most: a store root holding one
directory per project, each containing ``<session-id>.jsonl``. Codex is here
too, in its own unrelated rollout format, so tests that check platform routing
can prove a row's ``agent_type`` came from the store it was found in.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from lerim.adapters.trajectory_source import MIN_CONVERSATION_TURNS

BASE_TIME = datetime(2026, 3, 6, 14, 15, 0, tzinfo=timezone.utc)


def stamp(step: int) -> str:
    """Return the claude-code timestamp for step ``step`` of a transcript."""
    return (BASE_TIME + timedelta(seconds=step)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def user_line(step: int, text: str, *, cwd: str, branch: str) -> str:
    """Return one raw claude-code user turn."""
    return json.dumps(
        {
            "type": "user",
            "uuid": f"u{step}",
            "parentUuid": None,
            "isSidechain": False,
            "cwd": cwd,
            "gitBranch": branch,
            "timestamp": stamp(step),
            "message": {"role": "user", "content": text},
        }
    )


def assistant_line(step: int, text: str, *, thinking: str | None = None) -> str:
    """Return one raw claude-code assistant turn, optionally carrying reasoning."""
    blocks: list[dict[str, str]] = []
    if thinking is not None:
        blocks.append({"type": "thinking", "thinking": thinking})
    blocks.append({"type": "text", "text": text})
    return json.dumps(
        {
            "type": "assistant",
            "uuid": f"a{step}",
            "timestamp": stamp(step),
            "message": {
                "role": "assistant",
                "model": "claude-opus-5",
                "content": blocks,
            },
        }
    )


def tool_call_line(step: int, call_id: str, name: str, args: dict[str, str]) -> str:
    """Return one raw claude-code assistant turn issuing a tool call."""
    return json.dumps(
        {
            "type": "assistant",
            "uuid": f"a{step}",
            "timestamp": stamp(step),
            "message": {
                "role": "assistant",
                "model": "claude-opus-5",
                "content": [
                    {"type": "tool_use", "id": call_id, "name": name, "input": args}
                ],
            },
        }
    )


def tool_result_line(step: int, call_id: str, text: str) -> str:
    """Return one raw claude-code tool result."""
    return json.dumps(
        {
            "type": "user",
            "uuid": f"u{step}",
            "timestamp": stamp(step),
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": call_id,
                        "content": [{"type": "text", "text": text}],
                    }
                ],
            },
        }
    )


def abandoned_transcript() -> str:
    """Return a transcript with a user turn and no assistant turn.

    Upstream rejects it with ``missing_assistant_records``. It is the common
    shape on a real machine — 11 of 12 local sessions look like this — so every
    batch path has to tolerate it.
    """
    return (
        json.dumps(
            {
                "type": "user",
                "uuid": "u1",
                "timestamp": stamp(1),
                "message": {"role": "user", "content": "start something"},
            }
        )
        + "\n"
    )


def write_claude_session(
    root: Path,
    run_id: str,
    *,
    project: str = "-workspace-project",
    cwd: str = "/workspace/project",
    branch: str = "main",
    turns: int = MIN_CONVERSATION_TURNS,
    first_user_text: str = "fix the flaky retry test",
    tool_result: str = "1\tdef retry():",
    extra_lines: tuple[str, ...] = (),
) -> Path:
    """Write one claude-code transcript that normalizes to ``turns`` conversation records.

    ``turns`` counts user+assistant records the way
    :data:`~lerim.adapters.trajectory_source.MIN_CONVERSATION_TURNS` does, so a
    caller can place a session just above or just below the ingest threshold on
    purpose.
    """
    lines = [
        user_line(1, first_user_text, cwd=cwd, branch=branch),
        assistant_line(2, "Looking at retry.py now.", thinking="check retry"),
        tool_call_line(3, "toolu_01A", "Read", {"file_path": "retry.py"}),
        tool_result_line(4, "toolu_01A", tool_result),
    ]
    step = 5
    while _conversation_records_so_far(lines) < turns:
        if step % 2:
            lines.append(assistant_line(step, f"Step {step} done."))
        else:
            lines.append(user_line(step, f"and now step {step}", cwd=cwd, branch=branch))
        step += 1
    lines.extend(extra_lines)
    path = root / project / f"{run_id}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def write_abandoned_session(
    root: Path, run_id: str, *, project: str = "-workspace-project"
) -> Path:
    """Write a transcript the normalizer rejects, alongside real ones."""
    path = root / project / f"{run_id}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(abandoned_transcript(), encoding="utf-8")
    return path


def codex_stamp(step: int) -> str:
    """Return the codex rollout timestamp for step ``step``."""
    return (BASE_TIME + timedelta(seconds=step)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _codex_message(step: int, role: str, text: str) -> str:
    """Return one raw codex conversation message."""
    block = "input_text" if role == "user" else "output_text"
    return json.dumps(
        {
            "timestamp": codex_stamp(step),
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": role,
                "content": [{"type": block, "text": text}],
            },
        }
    )


def write_codex_session(
    root: Path,
    run_id: str,
    *,
    cwd: str = "/workspace/project",
    branch: str = "feature/retry",
    turns: int = MIN_CONVERSATION_TURNS,
    first_user_text: str = "create one focused commit",
) -> Path:
    """Write one codex rollout transcript that normalizes to ``turns`` turns.

    Codex stores rollouts under dated subdirectories, so the file is written
    nested to prove discovery walks the tree rather than globbing the root.
    """
    lines = [
        json.dumps(
            {
                "timestamp": codex_stamp(0),
                "type": "session_meta",
                "payload": {
                    "id": run_id,
                    "cwd": cwd,
                    "timestamp": codex_stamp(0),
                    "git": {"branch": branch},
                },
            }
        ),
        json.dumps(
            {
                "timestamp": codex_stamp(0),
                "type": "turn_context",
                "payload": {"cwd": cwd, "model": "gpt-5.3-codex"},
            }
        ),
        _codex_message(1, "user", first_user_text),
        json.dumps(
            {
                "timestamp": codex_stamp(2),
                "type": "event_msg",
                "payload": {"type": "agent_reasoning", "text": "Reviewing the diff"},
            }
        ),
        json.dumps(
            {
                "timestamp": codex_stamp(3),
                "type": "response_item",
                "payload": {
                    "type": "function_call",
                    "name": "shell",
                    "call_id": "call_A",
                    "arguments": '{"command":"git status"}',
                },
            }
        ),
        json.dumps(
            {
                "timestamp": codex_stamp(4),
                "type": "response_item",
                "payload": {
                    "type": "function_call_output",
                    "call_id": "call_A",
                    "output": "nothing to commit",
                },
            }
        ),
    ]
    step = 5
    written_turns = 1
    while written_turns < turns:
        role = "assistant" if step % 2 else "user"
        lines.append(_codex_message(step, role, f"codex step {step}"))
        written_turns += 1
        step += 1
    path = root / "2026" / "03" / "06" / f"{run_id}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _conversation_records_so_far(lines: list[str]) -> int:
    """Count the user/assistant records the written lines will normalize to."""
    total = 0
    for line in lines:
        content = json.loads(line)["message"]["content"]
        if (
            isinstance(content, list)
            and content
            and content[0].get("type") == "tool_result"
        ):
            continue
        total += 1
    return total
