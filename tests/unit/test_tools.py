"""Unit tests for the simplified Lerim agent tools."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic_ai import ModelRetry
from pydantic_ai.messages import ModelRequest, ModelResponse, SystemPromptPart, ToolCallPart, ToolReturnPart

from lerim.agents.tools import (
    CONTEXT_HARD_PRESSURE_PCT,
    CONTEXT_SOFT_PRESSURE_PCT,
    MODEL_CONTEXT_TOKEN_LIMIT,
    PRUNED_STUB,
    ContextDeps,
    Finding,
    archive_record,
    compute_request_budget,
    context_pressure_injector,
    context_query,
    create_record,
    fetch_records,
    list_records,
    note,
    notes_state_injector,
    prune,
    prune_history_processor,
    search_records,
    supersede_record,
    trace_read,
    update_record,
)
from lerim.context.project_identity import resolve_project_identity
from lerim.context.store import ContextStore


def _make_ctx(tmp_path: Path, *, trace_lines: list[str] | None = None):
    """Build a minimal RunContext-like object for tool tests."""
    trace_path = tmp_path / "trace.jsonl"
    if trace_lines is None:
        trace_lines = [f'{{"turn": {idx}, "content": "message {idx}"}}' for idx in range(1, 21)]
    trace_path.write_text("\n".join(trace_lines), encoding="utf-8")
    project_root = tmp_path / "repo"
    project_root.mkdir()
    context_db_path = tmp_path / "context.sqlite3"
    identity = resolve_project_identity(project_root)
    deps = ContextDeps(
        context_db_path=context_db_path,
        project_identity=identity,
        session_id="sess_test",
        project_ids=[identity.project_id],
        trace_path=trace_path,
        run_folder=tmp_path / "run",
    )
    store = ContextStore(context_db_path)
    store.initialize()
    store.register_project(identity)
    store.upsert_session(
        project_id=identity.project_id,
        session_id=deps.session_id,
        agent_type="codex",
        source_trace_ref=str(trace_path),
        repo_path=str(project_root),
        cwd=str(project_root),
        started_at="2026-04-17T00:00:00Z",
        model_name="test-model",
        instructions_text="test",
        prompt_text="test",
        metadata={},
    )
    return SimpleNamespace(deps=deps), store


def test_trace_read_reads_numbered_chunk(tmp_path) -> None:
    """trace_read returns a numbered chunk with pagination header."""
    ctx, _store = _make_ctx(tmp_path)

    result = trace_read(ctx, offset=2, limit=4)

    assert "[20 lines, showing 3-6]" in result
    assert "3\t" in result
    assert "6\t" in result


def test_trace_read_truncates_large_line(tmp_path) -> None:
    """trace_read truncates oversized lines instead of flooding the prompt."""
    huge_line = "x" * 20_000
    ctx, _store = _make_ctx(tmp_path, trace_lines=[huge_line, "short"])

    result = trace_read(ctx, offset=0, limit=2)

    assert "[2 lines, showing 1-2]" in result
    assert "truncated" in result
    assert "short" in result


def test_note_is_runtime_only(tmp_path) -> None:
    """note stores findings in run state only."""
    ctx, store = _make_ctx(tmp_path)

    result = note(
        ctx,
        [
            Finding(theme="caching", offset=4, quote="use redis", level="decision"),
            Finding(theme="caching", offset=8, quote="ttl needed", level="constraint"),
        ],
    )

    assert "Noted 2 findings" in result
    assert len(ctx.deps.notes) == 2
    with store.connect() as conn:
        tables = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "session_findings" not in tables


def test_create_search_fetch_update_archive_and_supersede(tmp_path) -> None:
    """Core read/write tools work together on the canonical store."""
    ctx, _store = _make_ctx(tmp_path)

    first = json.loads(
        create_record(
            ctx,
            kind="decision",
            title="Use Redis for cache",
            body="Use Redis as the cache backend with TTL support.",
            decision="Use Redis for cache",
            why="Need TTL and persistence",
        )
    )["result"]
    second = json.loads(
        create_record(
            ctx,
            kind="fact",
            title="Cache currently uses Redis",
            body="Production cache is Redis with write-time TTL.",
        )
    )["result"]

    search_payload = json.loads(search_records(ctx, query="redis ttl persistence"))
    assert search_payload["count"] >= 1
    assert any(hit["record_id"] == first["record_id"] for hit in search_payload["hits"])

    fetch_payload = json.loads(fetch_records(ctx, record_ids=[first["record_id"]], include_versions=True, response_format="detailed"))
    assert fetch_payload["count"] == 1
    assert fetch_payload["records"][0]["decision"] == "Use Redis for cache"

    updated = json.loads(
        update_record(
            ctx,
            record_id=second["record_id"],
            body="Production cache is Redis with TTL and eviction metrics.",
            change_reason="clarify runtime behavior",
        )
    )["result"]
    assert "eviction metrics" in updated["body"]

    with pytest.raises(ModelRetry, match="Use supersede_record"):
        archive_record(ctx, record_id=second["record_id"], reason="superseded fact")

    superseded = json.loads(
        supersede_record(
            ctx,
            record_id=first["record_id"],
            replacement_record_id=second["record_id"],
            reason="replaced by broader fact record",
        )
    )["result"]
    assert superseded["superseded_by_record_id"] == second["record_id"]
    assert superseded["valid_until"]


def test_archive_record_requests_model_retry_for_fresh_duplicate(tmp_path) -> None:
    """archive_record should ask the model to try supersession instead of hard-failing."""
    ctx, _store = _make_ctx(tmp_path)
    fresh = json.loads(
        create_record(
            ctx,
            kind="fact",
            title="Fresh duplicate",
            body="Temporary duplicate note about a durable decision.",
        )
    )["result"]

    with pytest.raises(ModelRetry, match="Use supersede_record"):
        archive_record(ctx, record_id=fresh["record_id"], reason="duplicate")


def test_create_record_rejects_invalid_episode(tmp_path) -> None:
    """Episode creation should require session fields."""
    ctx, _store = _make_ctx(tmp_path)

    with pytest.raises(ModelRetry, match="Episode records need both"):
        create_record(
            ctx,
            kind="episode",
            title="Session summary",
            body="Something happened.",
        )


def test_create_record_rejects_invalid_decision(tmp_path) -> None:
    """Decision creation should require both decision and why."""
    ctx, _store = _make_ctx(tmp_path)

    with pytest.raises(ModelRetry, match="Decision records need both"):
        create_record(
            ctx,
            kind="decision",
            title="Use Redis",
            body="Redis is the cache backend.",
            decision="Use Redis",
        )


def test_create_record_rejects_verbose_episode_recap(tmp_path) -> None:
    """Episode records should stay compact and avoid session-report narration."""
    ctx, _store = _make_ctx(tmp_path)

    with pytest.raises(ModelRetry, match="Episode body is too long"):
        create_record(
            ctx,
            kind="episode",
            title="Reviewed Lerim DB tool design",
            body="x" * 500,
            user_intent="Review the DB tool design.",
            what_happened="Compared typed tools and raw SQL access.",
        )


def test_context_query_lists_and_counts_records(tmp_path) -> None:
    """context_query supports deterministic analytics."""
    ctx, _store = _make_ctx(tmp_path)
    json.loads(create_record(ctx, kind="fact", title="First", body="one"))
    json.loads(create_record(ctx, kind="fact", title="Second", body="two"))

    count_payload = json.loads(context_query(ctx, entity="records", mode="count"))
    assert count_payload["count"] == 2

    list_payload = json.loads(
        context_query(
            ctx,
            entity="records",
            mode="list",
            order_by="created_at",
            limit=1,
            include_total=True,
        )
    )
    assert list_payload["count"] == 1
    assert list_payload["total"] == 2
    assert list_payload["rows"][0]["title"] == "Second"

    memory_count = json.loads(context_query(ctx, entity="memories", mode="count"))
    assert memory_count["count"] == 2


def test_list_records_browses_recent_matching_rows(tmp_path) -> None:
    """list_records should browse recent rows without semantic matching."""
    ctx, _store = _make_ctx(tmp_path)
    json.loads(create_record(ctx, kind="fact", title="First active fact", body="one"))
    json.loads(create_record(ctx, kind="episode", title="Routine sync", body="two", user_intent="sync", what_happened="ran sync"))

    payload = json.loads(list_records(ctx, status_filters=["active"], limit=10))

    assert payload["count"] == 2
    assert {row["title"] for row in payload["records"]} == {"First active fact", "Routine sync"}
    assert all("body_preview" in row for row in payload["records"])


def test_search_records_rejects_blank_query(tmp_path) -> None:
    """search_records should require a real search query."""
    ctx, _store = _make_ctx(tmp_path)

    with pytest.raises(ModelRetry, match="Use list_records"):
        search_records(ctx, query="")


def test_context_query_supports_date_filtering(tmp_path) -> None:
    """context_query filters records by creation time."""
    ctx, store = _make_ctx(tmp_path)
    record = json.loads(create_record(ctx, kind="fact", title="Today", body="fresh"))["result"]
    with store.connect() as conn:
        conn.execute("UPDATE records SET created_at = ?, updated_at = ? WHERE record_id = ?", ("2026-04-16T10:00:00+00:00", "2026-04-16T10:00:00+00:00", record["record_id"]))
        conn.execute("UPDATE record_versions SET created_at = ?, updated_at = ?, changed_at = ? WHERE record_id = ?", ("2026-04-16T10:00:00+00:00", "2026-04-16T10:00:00+00:00", "2026-04-16T10:00:00+00:00", record["record_id"]))

    payload = json.loads(
        context_query(
            ctx,
            entity="records",
            mode="list",
            created_since="2026-04-17T00:00:00+00:00",
            include_total=True,
        )
    )
    assert payload["count"] == 0
    assert payload["total"] == 0


def test_context_query_invalid_entity_requests_retry(tmp_path) -> None:
    """Malformed context_query inputs should guide the model to retry."""
    ctx, _store = _make_ctx(tmp_path)

    with pytest.raises(ModelRetry, match="entity must be one of"):
        context_query(ctx, entity="", mode="count")


def test_compute_request_budget_scales_with_trace_size(tmp_path) -> None:
    """Request budget grows with trace length."""
    short = tmp_path / "short.jsonl"
    short.write_text("x\n" * 40, encoding="utf-8")
    medium = tmp_path / "medium.jsonl"
    medium.write_text("x\n" * 400, encoding="utf-8")
    long = tmp_path / "long.jsonl"
    long.write_text("x\n" * 8000, encoding="utf-8")

    assert compute_request_budget(short) == 40
    assert compute_request_budget(medium) > 40
    assert compute_request_budget(long) == 100


def test_notes_state_injector_summarizes_notes(tmp_path) -> None:
    """Notes injector adds a compact notes dashboard."""
    ctx, _store = _make_ctx(tmp_path)
    ctx.deps.notes.extend(
        [
            Finding(theme="caching", offset=1, quote="redis", level="decision"),
            Finding(theme="caching", offset=2, quote="ttl", level="constraint"),
            Finding(theme="auth", offset=3, quote="oauth", level="fact"),
        ]
    )

    injected = notes_state_injector(ctx, [])

    assert isinstance(injected[-1], ModelRequest)
    content = injected[-1].parts[0].content
    assert "NOTES: 3 findings" in content
    assert "Top themes:" in content


def test_context_pressure_injector_reports_pressure(tmp_path) -> None:
    """Context injector reports approximate token pressure."""
    ctx, _store = _make_ctx(tmp_path)
    content = "x" * 1000
    history = [ModelRequest(parts=[SystemPromptPart(content=content)])]

    injected = context_pressure_injector(ctx, history)

    assert isinstance(injected[-1], ModelRequest)
    text = injected[-1].parts[0].content
    assert "CONTEXT:" in text
    approx_tokens = int((1000 * 0.25) + 0.999999999)
    pct = approx_tokens / MODEL_CONTEXT_TOKEN_LIMIT
    if pct >= CONTEXT_HARD_PRESSURE_PCT:
        assert "[hard]" in text
    elif pct >= CONTEXT_SOFT_PRESSURE_PCT:
        assert "[soft]" in text
    else:
        assert "[normal]" in text


def test_prune_history_processor_rewrites_pruned_trace_returns(tmp_path) -> None:
    """Pruned trace reads are replaced with a stub in history."""
    ctx, _store = _make_ctx(tmp_path)
    ctx.deps.pruned_offsets.add(10)
    history = [
        ModelRequest(parts=[ToolCallPart(tool_name="trace_read", args={"offset": 10}, tool_call_id="call-1")]),
        ModelResponse(parts=[ToolReturnPart(tool_name="trace_read", content="full trace", tool_call_id="call-1")]),
    ]

    rewritten = prune_history_processor(ctx, history)

    returned = rewritten[1].parts[0]
    assert isinstance(returned, ToolReturnPart)
    assert returned.content == PRUNED_STUB
