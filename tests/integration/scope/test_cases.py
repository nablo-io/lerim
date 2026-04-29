"""Integration cases for multi-project scope isolation."""

from __future__ import annotations

import pytest

from lerim.agents.extract import ExtractionResult
from lerim.agents.maintain import run_maintain
from lerim.config.providers import build_pydantic_model
from lerim.server.api import api_query
from lerim.server.runtime import LerimRuntime
from tests.integration.common_helpers import retry_on_overload
from tests.integration.scope.helpers import (
    ScopeCaseEnv,
    build_scope_case_env,
    load_scope_expectation,
    seed_scope_record,
    seed_scope_session,
)


def _seed_ask_scope_records(env: ScopeCaseEnv) -> None:
    """Seed one distinctive durable decision into each project."""
    seed_scope_session(
        env,
        project_identity=env.identity_a,
        session_id="scope-seed-alpha",
        repo_root=env.project_a_root,
    )
    seed_scope_session(
        env,
        project_identity=env.identity_b,
        session_id="scope-seed-beta",
        repo_root=env.project_b_root,
    )
    seed_scope_record(
        env,
        project_identity=env.identity_a,
        session_id="scope-seed-alpha",
        kind="decision",
        title="Alpha uses Redis leases for worker ownership",
        body=(
            "Project alpha keeps worker ownership in Redis lease keys. "
            "Renewals and failover use Redis lease expiration."
        ),
        decision="Use Redis leases for worker ownership in project alpha.",
        why="Lease expiration gives a simple recovery boundary for alpha workers.",
    )
    seed_scope_record(
        env,
        project_identity=env.identity_b,
        session_id="scope-seed-beta",
        kind="decision",
        title="Beta uses Postgres advisory locks for worker ownership",
        body=(
            "Project beta uses Postgres advisory locks for worker ownership. "
            "Workers rely on advisory lock handoff instead of Redis."
        ),
        decision="Use Postgres advisory locks for worker ownership in project beta.",
        why="Beta already coordinates its workers through Postgres.",
    )


def _seed_maintain_scope_records(env: ScopeCaseEnv) -> str:
    """Seed duplicate alpha records and one clean beta record for maintain."""
    seed_scope_session(
        env,
        project_identity=env.identity_a,
        session_id="maintain-seed-alpha",
        repo_root=env.project_a_root,
    )
    seed_scope_session(
        env,
        project_identity=env.identity_b,
        session_id="maintain-seed-beta",
        repo_root=env.project_b_root,
    )

    seed_scope_record(
        env,
        project_identity=env.identity_a,
        session_id="maintain-seed-alpha",
        kind="decision",
        title="Alpha keeps Redis leases authoritative",
        body=(
            "Project alpha treats Redis lease state as the authoritative worker "
            "ownership source."
        ),
        decision="Alpha worker ownership is tracked with Redis leases.",
        why="Alpha failover reads lease state from Redis.",
    )
    seed_scope_record(
        env,
        project_identity=env.identity_a,
        session_id="maintain-seed-alpha",
        kind="decision",
        title="Alpha worker ownership still lives in Redis leases",
        body=(
            "Same durable meaning as the other alpha decision: Redis leases are "
            "authoritative for worker ownership."
        ),
        decision="Redis leases remain the authority for alpha worker ownership.",
        why="The project uses lease expiry for takeover and recovery.",
    )
    seed_scope_record(
        env,
        project_identity=env.identity_a,
        session_id="maintain-seed-alpha",
        kind="episode",
        title="Routine alpha sync confirmation",
        body="Routine sync ran successfully with no durable context.",
        user_intent="Confirm the nightly sync completed.",
        what_happened="The operator checked the routine sync and saw no issues.",
        outcomes="No durable action was needed.",
    )
    beta_record = seed_scope_record(
        env,
        project_identity=env.identity_b,
        session_id="maintain-seed-beta",
        kind="decision",
        title="Beta keeps advisory locks in Postgres",
        body="Project beta coordinates workers with Postgres advisory locks.",
        decision="Use Postgres advisory locks for beta workers.",
        why="Beta already coordinates durable worker state in Postgres.",
    )["record_id"]
    return beta_record


@pytest.mark.integration
@pytest.mark.llm
@pytest.mark.agent
def test_ask_project_scope_ignores_other_projects(live_config, tmp_path) -> None:
    """Project-scoped ask should not pull facts from a different project."""
    expectation = load_scope_expectation("ask_project_scope_ignores_other_projects")["expected"]
    env = build_scope_case_env(live_config=live_config, tmp_path=tmp_path)
    _seed_ask_scope_records(env)
    runtime = LerimRuntime(default_cwd=str(env.project_a_root), config=env.config)

    answer, _session_id, _cost_usd, _debug = runtime.ask(
        "What worker ownership mechanism does this project use?",
        project_ids=[env.identity_a.project_id],
        repo_root=env.project_a_root,
        include_debug=True,
    )

    lowered = answer.lower()
    for token in expectation["answer_must_include_all"]:
        assert token in lowered
    for token in expectation["answer_must_not_include"]:
        assert token not in lowered


@pytest.mark.integration
@pytest.mark.llm
@pytest.mark.agent
def test_ask_all_scope_can_combine_projects(live_config, tmp_path) -> None:
    """All-project ask should be able to synthesize across both projects."""
    expectation = load_scope_expectation("ask_all_scope_can_combine_projects")["expected"]
    env = build_scope_case_env(live_config=live_config, tmp_path=tmp_path)
    _seed_ask_scope_records(env)
    runtime = LerimRuntime(default_cwd=str(env.project_a_root), config=env.config)

    answer, _session_id, _cost_usd, _debug = runtime.ask(
        "Across the registered projects, what worker ownership mechanisms do we use?",
        project_ids=env.all_project_ids,
        repo_root=env.project_a_root,
        include_debug=True,
    )

    lowered = answer.lower()
    for token in expectation["answer_must_include_all"]:
        assert token in lowered


@pytest.mark.integration
@pytest.mark.llm
@pytest.mark.agent
def test_maintain_project_a_only_mutates_project_a(live_config, tmp_path) -> None:
    """Maintain should only write versions inside the selected project scope."""
    expectation = load_scope_expectation("maintain_project_a_only_mutates_project_a")["expected"]
    env = build_scope_case_env(live_config=live_config, tmp_path=tmp_path)
    beta_record_id = _seed_maintain_scope_records(env)
    seed_scope_session(
        env,
        project_identity=env.identity_a,
        session_id="maintain-alpha-run",
        repo_root=env.project_a_root,
        agent_type="maintain",
    )
    model = build_pydantic_model("agent", config=env.config)

    result = retry_on_overload(
        lambda: run_maintain(
            context_db_path=env.config.context_db_path,
            project_identity=env.identity_a,
            session_id="maintain-alpha-run",
            model=model,
        )
    )

    with env.store.connect() as conn:
        changed_rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT rv.record_id, rv.change_kind, r.project_id
                FROM record_versions AS rv
                JOIN records AS r ON r.record_id = rv.record_id
                WHERE rv.changed_by_session_id = ?
                ORDER BY rv.changed_at ASC, rv.version_no ASC
                """,
                ("maintain-alpha-run",),
            ).fetchall()
        ]

    beta_record = env.store.fetch_record(
        beta_record_id,
        project_ids=[env.identity_b.project_id],
        include_versions=True,
    )

    assert result.completion_summary.strip()
    assert changed_rows, "maintain should make at least one scoped repair in project alpha"
    assert all(row["project_id"] == env.identity_a.project_id for row in changed_rows)
    assert beta_record is not None
    assert len(beta_record["versions"]) == 1
    assert beta_record["status"] == "active"
    assert expectation["changed_project"] == env.project_a_name
    assert expectation["untouched_project"] == env.project_b_name


@pytest.mark.integration
def test_query_scope_matches_ask_scope_rules(
    live_config,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deterministic query scope selection should mirror ask scope selection."""
    expectation = load_scope_expectation("query_scope_matches_ask_scope_rules")["expected"]
    env = build_scope_case_env(live_config=live_config, tmp_path=tmp_path)
    _seed_ask_scope_records(env)
    monkeypatch.setattr("lerim.server.api.get_config", lambda: env.config)

    project_payload = api_query(
        entity="records",
        mode="count",
        scope="project",
        project=env.project_a_name,
        kind="decision",
    )
    all_payload = api_query(
        entity="records",
        mode="count",
        scope="all",
        kind="decision",
    )

    assert project_payload["error"] is False
    assert project_payload["projects_used"] == [env.project_a_name]
    assert project_payload["count"] == int(expectation["project_count"])
    assert project_payload["scope"] == "project"

    assert all_payload["error"] is False
    assert set(all_payload["projects_used"]) == {
        env.project_a_name,
        env.project_b_name,
    }
    assert all_payload["count"] == int(expectation["all_count"])
    assert all_payload["scope"] == "all"


@pytest.mark.integration
def test_scope_extract_project_a_does_not_touch_project_b(
    live_config,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Project-scoped sync/extract should mutate only the selected project."""
    expectation = load_scope_expectation("scope_extract_project_a_does_not_touch_project_b")["expected"]
    env = build_scope_case_env(live_config=live_config, tmp_path=tmp_path)
    seed_scope_session(
        env,
        project_identity=env.identity_b,
        session_id="extract-beta-seed",
        repo_root=env.project_b_root,
    )
    beta_record_id = seed_scope_record(
        env,
        project_identity=env.identity_b,
        session_id="extract-beta-seed",
        kind="fact",
        title="Beta control fact",
        body="Beta control fact should remain untouched.",
    )["record_id"]
    runtime = LerimRuntime(default_cwd=str(env.project_a_root), config=env.config)
    trace_path = env.project_a_root / "scope-extract-trace.jsonl"
    trace_path.write_text('{"role":"user","content":"scope extract test"}\n', encoding="utf-8")

    monkeypatch.setattr("lerim.server.runtime.build_pydantic_model", lambda *args, **kwargs: "fake-model")

    def _fake_run_extraction(**kwargs):
        env.store.create_record(
            project_id=kwargs["project_identity"].project_id,
            session_id=kwargs["session_id"],
            kind="fact",
            title="Alpha extracted fact",
            body="Alpha extracted fact should land only in alpha.",
            change_reason="scope_extract_create",
        )
        env.store.create_record(
            project_id=kwargs["project_identity"].project_id,
            session_id=kwargs["session_id"],
            kind="decision",
            title="Alpha extracted decision",
            body="Alpha extracted decision about scoped sync behavior.",
            decision="Keep scoped sync writes inside alpha only.",
            why="Project-scoped extraction should not mutate beta records.",
            change_reason="scope_extract_update",
        )
        return (ExtractionResult(completion_summary="scope extract complete"), [])

    monkeypatch.setattr("lerim.server.runtime.run_extraction", _fake_run_extraction)

    result = runtime.sync(trace_path=trace_path, session_id="extract-alpha-run")
    assert result["project_id"] == env.identity_a.project_id

    with env.store.connect() as conn:
        changed_rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT rv.record_id, rv.change_kind, r.project_id
                FROM record_versions AS rv
                JOIN records AS r ON r.record_id = rv.record_id
                WHERE rv.changed_by_session_id = ?
                ORDER BY rv.changed_at ASC, rv.version_no ASC
                """,
                ("extract-alpha-run",),
            ).fetchall()
        ]

    beta_record = env.store.fetch_record(
        beta_record_id,
        project_ids=[env.identity_b.project_id],
        include_versions=True,
    )

    assert len(changed_rows) == expectation["records_created_in_alpha"] + expectation["records_updated_in_alpha"]
    assert all(row["project_id"] == env.identity_a.project_id for row in changed_rows)
    assert beta_record is not None
    assert len(beta_record["versions"]) == expectation["beta_version_count"]
    assert expectation["changed_project"] == env.project_a_name
    assert expectation["untouched_project"] == env.project_b_name
