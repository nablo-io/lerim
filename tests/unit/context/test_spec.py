"""Unit tests for src/lerim/context/spec.py."""

from __future__ import annotations

import pytest

import lerim.context.spec as spec
from lerim.context.spec import (
    ALLOWED_CHANGE_KINDS,
    ALLOWED_KINDS,
    ALLOWED_ROLES,
    ALLOWED_STATUSES,
    DURABLE_RECORD_KINDS,
    MAX_RECORD_TITLE_CHARS,
    RecordChangeKind,
    RecordKind,
    RecordStatus,
    format_durable_record_kinds,
    normalize_record_payload,
    record_search_text,
    record_validation_message,
)


def _payload(**overrides):
    defaults = dict(
        kind="fact",
        title="A project fact",
        body="This fact is reusable project context.",
        status="active",
        source_session_id="sess_1",
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
        valid_from="2026-01-01T00:00:00+00:00",
        valid_until=None,
        superseded_by_record_id=None,
        decision=None,
        why=None,
        alternatives=None,
        consequences=None,
        user_intent=None,
        what_happened=None,
        outcomes=None,
    )
    defaults.update(overrides)
    return defaults


def test_allowed_kinds_include_episode_but_durable_kinds_do_not():
    assert "episode" in ALLOWED_KINDS
    assert "episode" not in DURABLE_RECORD_KINDS


def test_allowed_value_tuples_follow_domain_enums():
    assert ALLOWED_KINDS == tuple(kind.value for kind in RecordKind)
    assert ALLOWED_STATUSES == tuple(status.value for status in RecordStatus)
    assert ALLOWED_CHANGE_KINDS == tuple(kind.value for kind in RecordChangeKind)
    assert "procedure" in ALLOWED_ROLES


def test_format_durable_record_kinds_is_human_readable():
    text = format_durable_record_kinds()
    assert "decision" in text
    assert "episode" not in text


def test_validation_message_uses_context_terminology():
    message = record_validation_message("title_too_long")
    assert message is not None
    assert "context title" in message
    assert "memory title" not in message


def test_normalize_payload_trims_and_clears_irrelevant_typed_fields():
    payload = normalize_record_payload(
        **_payload(
            title="  A project fact  ",
            body="  Reusable body.  ",
            decision="not allowed on facts",
        )
    )
    assert payload["title"] == "A project fact"
    assert payload["body"] == "Reusable body."
    assert payload["decision"] is None


def test_archived_payload_gets_valid_until_from_updated_at():
    payload = normalize_record_payload(**_payload(status="archived", valid_until=None))
    assert payload["valid_until"] == payload["updated_at"]


def test_normalize_payload_uses_one_now_for_default_timestamps(monkeypatch):
    calls = []

    def fake_now():
        calls.append("called")
        return "2026-02-03T04:05:06+00:00"

    monkeypatch.setattr(spec, "_utc_now", fake_now)
    payload = normalize_record_payload(
        **_payload(created_at=None, updated_at=None, valid_from=None)
    )
    assert calls == ["called"]
    assert payload["created_at"] == "2026-02-03T04:05:06+00:00"
    assert payload["updated_at"] == "2026-02-03T04:05:06+00:00"
    assert payload["valid_from"] == "2026-02-03T04:05:06+00:00"


def test_episode_requires_session_id_and_required_fields():
    with pytest.raises(ValueError, match="episode_requires_session_id"):
        normalize_record_payload(
            **_payload(
                kind="episode",
                source_session_id=None,
                user_intent="Summarize the session.",
                what_happened="The session was summarized.",
            )
        )

    with pytest.raises(ValueError, match="episode_requires_user_intent_and_what_happened"):
        normalize_record_payload(**_payload(kind="episode"))


def test_decision_requires_decision_and_why():
    with pytest.raises(ValueError, match="decision_requires_decision_and_why"):
        normalize_record_payload(**_payload(kind="decision", decision="Use SQLite", why=None))


def test_title_length_is_enforced():
    with pytest.raises(ValueError, match="title_too_long"):
        normalize_record_payload(**_payload(title="x" * (MAX_RECORD_TITLE_CHARS + 1)))


def test_record_search_text_includes_typed_fields():
    payload = normalize_record_payload(
        **_payload(
            kind="decision",
            decision="Use SQLite.",
            why="It keeps local context simple.",
        )
    )
    text = record_search_text(payload)
    assert "kind: decision" in text
    assert "decision: Use SQLite." in text
    assert "why: It keeps local context simple." in text


def test_operational_role_payload_is_normalized_and_searchable():
    payload = normalize_record_payload(
        **_payload(
            record_role="failure-mode",
            role_payload={
                "failure_step": "Trace replay",
                "wrong_assumption": "Every replay can create a new episode.",
                "correction": "Treat duplicate episodes as idempotent.",
                "extra": "ignored",
            },
        )
    )

    text = record_search_text(payload)

    assert payload["record_role"] == "failure_mode"
    assert "extra" not in payload["role_payload"]
    assert "role: failure_mode" in text
    assert "wrong_assumption: Every replay can create a new episode." in text
