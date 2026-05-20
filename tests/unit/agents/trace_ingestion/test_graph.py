"""Tests for the BAML-backed extract graph."""

from __future__ import annotations

import json

from lerim.agents.trace_ingestion.api import run_trace_ingestion
from lerim.agents.trace_ingestion.graph import (
    _apply_coding_retention_decisions,
    _coding_eval_polish_to_synthesized,
    _episode_summary,
    _implementation_summary,
    _prioritize_coding_records,
)
from lerim.agents.trace_ingestion.windowing import TRACE_MAX_CHUNK_BYTES, read_trace_window
from lerim.context import ContextStore, resolve_project_identity
from tests.helpers import make_config


class FakeBamlRuntime:
    """BAML client double that exercises scan, filter, and synthesize phases."""

    def __init__(self) -> None:
        """Track the filtered summary passed into synthesis."""
        self.synthesis_input = ""
        self.synthesis_episode_summary = ""

    def ObserveSourceWindow(self, **_kwargs):
        """Return one strong candidate and one source-local candidate."""
        return {
            "episode_update": "The source session evaluated extraction filtering.",
            "durable_findings": [
                {
                    "theme": "general source filtering",
                    "kind": "decision",
                    "note": "Extraction should filter durable signal before synthesis.",
                },
                {
                    "theme": "local command output",
                    "kind": "fact",
                    "note": "A one-run command produced a local output.",
                },
            ],
            "implementation_findings": [
                {
                    "theme": "command output",
                    "note": "The command output is local evidence only.",
                }
            ],
            "discarded_noise": ["local command transcript"],
        }

    def FilterDurableSignal(self, **kwargs):
        """Keep only reusable signal."""
        assert "local command output" in kwargs["durable_findings_summary"]
        return {
            "kept_durable_findings": [
                {
                    "theme": "general trace filtering",
                    "kind": "decision",
                    "note": "Extraction should filter durable signal before synthesis.",
                }
            ],
            "rejected_findings": [
                {
                    "theme": "local command output",
                    "kind": "fact",
                    "note": "A one-run command produced a local output.",
                }
            ],
            "filtering_summary": "Kept reusable extraction policy and rejected local evidence.",
        }

    def SynthesizeContextRecords(self, **kwargs):
        """Assert synthesis sees the filtered candidates only."""
        self.synthesis_input = kwargs["durable_findings_summary"]
        self.synthesis_episode_summary = kwargs["episode_summary"]
        assert "general trace filtering" in self.synthesis_input
        assert "local command output" not in self.synthesis_input
        assert "local command output" not in self.synthesis_episode_summary
        assert "local command transcript" not in self.synthesis_episode_summary
        return {
            "completion_summary": "Extraction completed.",
            "episode": {
                "title": "Trace filtering extraction",
                "body": "The trace was scanned, filtered, and persisted.",
                "status": "active",
                "user_intent": "Extract durable signal.",
                "what_happened": "The graph filtered candidates before synthesis.",
                "outcomes": "One durable decision was created.",
            },
            "durable_records": [
                {
                    "kind": "decision",
                    "title": "Filter trace signal before synthesis",
                    "body": "Lerim should keep reusable trace signal and reject local evidence before writing durable records.",
                    "status": "active",
                    "decision": "Filter durable trace candidates before synthesis.",
                    "why": "This prevents local evidence from becoming future-agent context.",
                }
            ],
        }

    def GuardSynthesizedContextRecords(self, **kwargs):
        """Assert the final guard sees the draft and returns persisted records."""
        draft = json.loads(kwargs["draft_records_json"])
        assert draft["durable_records"][0]["title"] == "Filter trace signal before synthesis"
        return draft

    def PolishContextRecords(self, **kwargs):
        """Assert the polish pass sees the guarded draft before persistence."""
        assert "general trace filtering" in kwargs["durable_findings_summary"]
        assert "local command output" in kwargs["rejected_findings_summary"]
        assert "command output is local evidence" in kwargs["implementation_summary"]
        draft = json.loads(kwargs["draft_records_json"])
        assert draft["durable_records"][0]["title"] == "Filter trace signal before synthesis"
        return draft

    def PolishCodingEvalContextRecords(self, **kwargs):
        """Coding profile polish can pass through generic coding records."""
        assert "general trace filtering" in kwargs["durable_findings_summary"]
        assert "local command output" in kwargs["rejected_findings_summary"]
        assert "command output is local evidence" in kwargs["implementation_summary"]
        assert "line:1 user: generalize trace extraction" in kwargs["visible_source_lines"]
        assert "line:2 assistant: ran a local command" in kwargs["visible_source_lines"]
        draft = json.loads(kwargs["draft_records_json"])
        assert draft["durable_records"][0]["title"] == "Filter trace signal before synthesis"
        return {
            "episode": draft["episode"],
            "project_identity_fact": None,
            "model_setting_fact": None,
            "adapter_decision": None,
            "prompt_structure_decision": None,
            "fixture_constraint": None,
            "deferred_design_fact": None,
            "other_records": draft["durable_records"],
            "completion_summary": draft["completion_summary"],
        }

    def ExtractCodingStrategySlots(self, **kwargs):
        """Visible user strategy extraction can return no extra records."""
        assert "line:1 user: generalize trace extraction" in kwargs["user_source_lines"]
        return {
            "silent_change_feedback_record": None,
            "model_size_priority_record": None,
            "provider_cost_record": None,
            "role_split_record": None,
        }


class NoDurableFakeBamlRuntime:
    """BAML double for no-signal sessions with discarded source details."""

    def ObserveSourceWindow(self, **_kwargs):
        """Return only implementation and discarded details."""
        return {
            "episode_update": "The user marked browser console CSS selectors as temporary debugging.",
            "durable_findings": [],
            "implementation_findings": [
                {
                    "theme": "temporary browser debugging",
                    "note": "Browser console selector names were temporary source-local evidence.",
                }
            ],
            "discarded_noise": ["browser console CSS selectors"],
        }

    def FilterDurableSignal(self, **kwargs):
        """Assert filtering can see noise, but keeps no durable signal."""
        assert "browser console CSS selectors" in kwargs["implementation_summary"]
        return {
            "kept_durable_findings": [],
            "rejected_findings": [],
            "filtering_summary": "No reusable durable signal.",
        }

    def SynthesizeContextRecords(self, **kwargs):
        """Synthesis should not receive source-local artifact names."""
        assert "CSS" not in kwargs["episode_summary"]
        assert "browser console" not in kwargs["episode_summary"]
        return {
            "completion_summary": "No reusable context found.",
            "episode": {
                "title": "No reusable context",
                "body": "The source session did not contain reusable project context.",
                "status": "archived",
                "user_intent": "Ingest the source session.",
                "what_happened": "The trace was scanned and no reusable durable context was found.",
                "outcomes": "No durable records were created.",
            },
            "durable_records": [],
        }

    def GuardSynthesizedContextRecords(self, **kwargs):
        """No-signal sessions still pass through the guard before persistence."""
        draft = json.loads(kwargs["draft_records_json"])
        assert draft["durable_records"] == []
        assert "browser console" in kwargs["implementation_summary"]
        return draft

    def PolishContextRecords(self, **kwargs):
        """No-signal sessions still pass through the polish step."""
        assert "browser console" in kwargs["implementation_summary"]
        assert kwargs["durable_findings_summary"] == "(none)"
        draft = json.loads(kwargs["draft_records_json"])
        assert draft["durable_records"] == []
        return draft

    def PolishCodingEvalContextRecords(self, **kwargs):
        """No-signal coding sessions still pass through the polish step."""
        assert "browser console" in kwargs["implementation_summary"]
        assert kwargs["durable_findings_summary"] == "(none)"
        assert "line:1 user: temporary browser debugging" in kwargs["visible_source_lines"]
        draft = json.loads(kwargs["draft_records_json"])
        assert draft["durable_records"] == []
        return {
            "episode": draft["episode"],
            "project_identity_fact": None,
            "model_setting_fact": None,
            "adapter_decision": None,
            "prompt_structure_decision": None,
            "fixture_constraint": None,
            "deferred_design_fact": None,
            "other_records": [],
            "completion_summary": draft["completion_summary"],
        }

    def ExtractCodingStrategySlots(self, **kwargs):
        """No-signal user strategy extraction can return no extra records."""
        assert "temporary browser debugging" in kwargs["user_source_lines"]
        return {
            "silent_change_feedback_record": None,
            "model_size_priority_record": None,
            "provider_cost_record": None,
            "role_split_record": None,
        }


class CodingPolishDropsFakeBamlRuntime:
    """BAML double where coding polish intentionally rejects synthesized noise."""

    def ObserveSourceWindow(self, **_kwargs):
        """Return a weak implementation candidate that synthesis over-keeps."""
        return {
            "episode_update": "The source session executed an implementation plan.",
            "durable_findings": [
                {
                    "theme": "implementation plan detail",
                    "kind": "fact",
                    "line": 1,
                    "note": "A one-off implementation plan mentioned module wiring.",
                }
            ],
            "implementation_findings": [],
            "discarded_noise": [],
        }

    def FilterDurableSignal(self, **_kwargs):
        """Let a weak candidate through so polish can make the final call."""
        return {
            "kept_durable_findings": [
                {
                    "theme": "implementation plan detail",
                    "kind": "fact",
                    "line": 1,
                    "note": "A one-off implementation plan mentioned module wiring.",
                }
            ],
            "rejected_findings": [],
            "filtering_summary": "Candidate passed initial filter.",
        }

    def SynthesizeContextRecords(self, **_kwargs):
        """Return a noisy durable record that should not survive coding polish."""
        return {
            "completion_summary": "Synthesized noisy candidate.",
            "episode": {
                "title": "Implementation plan",
                "body": "The session executed a one-off implementation plan.",
                "status": "archived",
                "user_intent": "Run an implementation plan.",
                "what_happened": "A candidate implementation detail was synthesized.",
                "outcomes": "No durable guidance should remain.",
            },
            "durable_records": [
                {
                    "kind": "fact",
                    "title": "Module wiring detail",
                    "body": "The one-off plan mentioned module wiring.",
                    "status": "active",
                    "source_event_refs": ["line:1"],
                }
            ],
        }

    def GuardSynthesizedContextRecords(self, **kwargs):
        """The coding budget path skips this in the graph-level test."""
        return json.loads(kwargs["draft_records_json"])

    def PolishCodingEvalContextRecords(self, **kwargs):
        """Reject all noisy coding records without fallback resurrection."""
        draft = json.loads(kwargs["draft_records_json"])
        assert draft["durable_records"][0]["title"] == "Module wiring detail"
        return {
            "episode": draft["episode"],
            "project_identity_fact": None,
            "model_setting_fact": None,
            "adapter_decision": None,
            "prompt_structure_decision": None,
            "fixture_constraint": None,
            "deferred_design_fact": None,
            "other_records": [],
            "completion_summary": "No reusable context found.",
        }

    def ExtractCodingStrategySlots(self, **kwargs):
        """No user strategy should be restored for this implementation-only task."""
        assert "one-off implementation plan" in kwargs["user_source_lines"]
        return {
            "silent_change_feedback_record": None,
            "model_size_priority_record": None,
            "provider_cost_record": None,
            "role_split_record": None,
        }


def test_extract_graph_filters_candidates_before_synthesis(tmp_path, monkeypatch):
    """The graph applies an explicit signal-filter phase before record synthesis."""
    fake_runtime = FakeBamlRuntime()
    monkeypatch.setattr(
        "lerim.agents.trace_ingestion.graph.build_baml_client_for_role",
        lambda **_kwargs: fake_runtime,
    )
    trace_path = tmp_path / "trace.jsonl"
    trace_path.write_text(
        '{"role":"user","content":"generalize trace extraction"}\n'
        '{"role":"assistant","content":"ran a local command"}\n',
        encoding="utf-8",
    )
    project_identity = resolve_project_identity(tmp_path)
    result, details = run_trace_ingestion(
        context_db_path=tmp_path / "context.sqlite3",
        project_identity=project_identity,
        session_id="session-1",
        trace_path=trace_path,
        config=make_config(tmp_path / ".lerim"),
        return_details=True,
        max_llm_calls=5,
    )

    assert result.completion_summary == "Trace ingestion completed: 1 durable record created."
    assert [event.action for event in details.events] == [
        "resolve_scope",
        "read_window",
        "scan_window",
        "filter_signals",
        "synthesize_records",
        "guard_records",
        "polish_records",
        "save_context",
        "save_context",
        "final_result",
    ]
    assert details.events[3].content == "kept=1 rejected=1"

    store = ContextStore(tmp_path / "context.sqlite3")
    rows = store.query(
        entity="records",
        mode="list",
        project_ids=[project_identity.project_id],
        include_archived=True,
        limit=10,
    )["rows"]
    assert sorted(row["kind"] for row in rows) == ["decision", "episode"]


def test_coding_fixture_slot_preserves_source_backed_body(tmp_path):
    """Fixture constraints keep the actual constraint instead of a generic eval rule."""
    trace_path = tmp_path / "trace.jsonl"
    trace_path.write_text('{"role":"assistant","content":"source evidence"}\n', encoding="utf-8")

    payload = _coding_eval_polish_to_synthesized(
        {
            "episode": {"title": "Qwen context-window debugging", "status": "active"},
            "model_setting_fact": None,
            "adapter_decision": None,
            "prompt_structure_decision": None,
            "fixture_constraint": {
                "title": "4B-8bit context window capped at ~250K tokens",
                "body": (
                    "Qwen3.5-4B-8bit has a 262K token context window. "
                    "Config max_window_tokens=300000 exceeds this and can crash Metal."
                ),
                "status": "active",
                "source_event_refs": ["line:1"],
            },
            "deferred_design_fact": None,
            "other_records": [],
            "completion_summary": "Done.",
        },
        trace_path=trace_path,
    )

    [record] = payload["durable_records"]
    assert record["kind"] == "constraint"
    assert "262K token context window" in record["body"]
    assert "extractable durable content" not in record["body"]


def test_coding_project_identity_repairs_source_ref_to_identity_url(tmp_path):
    """Project identity facts should cite the visible line that states the URL."""
    trace_path = tmp_path / "trace.jsonl"
    trace_path.write_text(
        "\n".join(
            [
                '{"message":{"role":"assistant","content":[{"type":"text","text":"Now I see the real issue."}]}}',
                '{"message":{"role":"assistant","content":[{"type":"text","text":"The project_url is https://example.com/acme/lerim and project_name is lerim."}]}}',
            ]
        ),
        encoding="utf-8",
    )

    payload = _coding_eval_polish_to_synthesized(
        {
            "episode": {"title": "Project identity", "status": "active"},
            "project_identity_fact": {
                "title": "Service project identity",
                "body": "The service project is lerim at https://example.com/acme/lerim.",
                "status": "active",
                "source_event_refs": ["line:1"],
            },
            "model_setting_fact": None,
            "adapter_decision": None,
            "prompt_structure_decision": None,
            "fixture_constraint": None,
            "deferred_design_fact": None,
            "other_records": [],
            "completion_summary": "Done.",
        },
        trace_path=trace_path,
    )

    [record] = payload["durable_records"]
    assert record["kind"] == "fact"
    assert record["source_event_refs"] == ["line:2"]


def test_coding_project_identity_limits_lower_level_setup_records(tmp_path):
    """Project identity sessions keep the accepted setup decision, not every diagnostic."""
    trace_path = tmp_path / "trace.jsonl"
    trace_path.write_text(
        "\n".join(
            [
                '{"message":{"role":"assistant","content":[{"type":"text","text":"Service project URL is https://example.com/acme/lerim."}]}}',
                '{"message":{"role":"assistant","content":[{"type":"text","text":"Use the env var forwarding setup."}]}}',
                '{"message":{"role":"assistant","content":[{"type":"text","text":"Diagnostic detail one."}]}}',
                '{"message":{"role":"assistant","content":[{"type":"text","text":"Diagnostic detail two."}]}}',
            ]
        ),
        encoding="utf-8",
    )

    payload = _coding_eval_polish_to_synthesized(
        {
            "episode": {"title": "Project identity", "status": "active"},
            "project_identity_fact": {
                "title": "Service project identity",
                "body": "Service project URL is https://example.com/acme/lerim.",
                "status": "active",
                "source_event_refs": ["line:1"],
            },
            "model_setting_fact": None,
            "adapter_decision": None,
            "prompt_structure_decision": None,
            "fixture_constraint": None,
            "deferred_design_fact": None,
            "other_records": [
                {
                    "kind": "fact",
                    "title": "Diagnostic detail one",
                    "body": "Diagnostic detail one.",
                    "status": "active",
                    "source_event_refs": ["line:3"],
                },
                {
                    "kind": "decision",
                    "title": "Use env var setup",
                    "body": "Use the env var forwarding setup.",
                    "status": "active",
                    "source_event_refs": ["line:2"],
                },
                {
                    "kind": "fact",
                    "title": "Diagnostic detail two",
                    "body": "Diagnostic detail two.",
                    "status": "active",
                    "source_event_refs": ["line:4"],
                },
            ],
            "completion_summary": "Done.",
        },
        trace_path=trace_path,
    )

    assert [record["title"] for record in payload["durable_records"]] == [
        "Service project identity",
        "Use env var setup",
    ]
    assert all("_slot_field" not in record for record in payload["durable_records"])


def test_coding_other_records_drop_initial_task_only_details(tmp_path):
    """Lower-level coding records need support beyond the initial task request."""
    trace_path = tmp_path / "trace.jsonl"
    trace_path.write_text(
        "\n".join(
            [
                '{"message":{"role":"user","content":"Implement the detailed plan in module_a.py."}}',
                '{"message":{"role":"assistant","content":[{"type":"text","text":"Later accepted decision: keep public docs separate from private launch notes."}]}}',
            ]
        ),
        encoding="utf-8",
    )

    payload = _coding_eval_polish_to_synthesized(
        {
            "episode": {"title": "Implementation plan", "status": "active"},
            "project_identity_fact": None,
            "model_setting_fact": None,
            "adapter_decision": None,
            "prompt_structure_decision": None,
            "fixture_constraint": None,
            "deferred_design_fact": None,
            "other_records": [
                {
                    "kind": "fact",
                    "title": "module_a implementation plan",
                    "body": "The implementation task requested changes in module_a.py.",
                    "status": "active",
                    "source_event_refs": ["line:1"],
                },
                {
                    "kind": "decision",
                    "title": "Separate public docs and launch notes",
                    "body": "Later accepted decision: keep public docs separate from private launch notes.",
                    "status": "active",
                    "source_event_refs": ["line:2"],
                },
            ],
            "completion_summary": "Done.",
        },
        trace_path=trace_path,
    )

    assert [record["title"] for record in payload["durable_records"]] == [
        "Separate public docs and launch notes"
    ]


def test_coding_records_drop_failed_tool_followup_debugging(tmp_path):
    """Failed-tool follow-up explanations are local debugging evidence."""
    trace_path = tmp_path / "trace.jsonl"
    trace_path.write_text(
        "\n".join(
            [
                '{"message":{"role":"assistant","content":[{"type":"tool_use","id":"toolu_1","name":"Bash","input":{}}]}}',
                '{"message":{"role":"user","content":[{"type":"tool_result","tool_use_id":"toolu_1","content":"failed","is_error":true}]}}',
                '{"message":{"role":"assistant","content":[{"type":"text","text":"The failing test needs repo_path fixture wiring."}]}}',
                '{"message":{"role":"assistant","content":[{"type":"text","text":"The empty evaluation fixture caused extract failures."}]}}',
            ]
        ),
        encoding="utf-8",
    )

    payload = _coding_eval_polish_to_synthesized(
        {
            "episode": {"title": "Failed tool follow-up", "status": "active"},
            "project_identity_fact": None,
            "model_setting_fact": None,
            "adapter_decision": None,
            "prompt_structure_decision": None,
            "fixture_constraint": {
                "title": "Fixture adequacy",
                "body": "The empty evaluation fixture caused extract failures.",
                "status": "active",
                "source_event_refs": ["line:4"],
            },
            "deferred_design_fact": None,
            "other_records": [
                {
                    "kind": "constraint",
                    "title": "Repo path fixture wiring",
                    "body": "The failing test needs repo_path fixture wiring.",
                    "status": "active",
                    "source_event_refs": ["line:3"],
                }
            ],
            "completion_summary": "Done.",
        },
        trace_path=trace_path,
    )

    assert [record["title"] for record in payload["durable_records"]] == [
        "Extract test fixture adequacy rule"
    ]


def test_coding_unilateral_code_edit_execution_archives_without_records(tmp_path):
    """One-way code-edit execution without user feedback should not create memories."""
    trace_path = tmp_path / "trace.jsonl"
    trace_path.write_text(
        "\n".join(
            [
                '{"message":{"role":"user","content":"Implement this detailed plan."}}',
                '{"message":{"role":"assistant","content":[{"type":"tool_use","id":"toolu_1","name":"Edit","input":{}}]}}',
                '{"message":{"role":"assistant","content":[{"type":"text","text":"The implementation now routes sessions by cwd."}]}}',
            ]
        ),
        encoding="utf-8",
    )

    payload = _coding_eval_polish_to_synthesized(
        {
            "episode": {"title": "Routing implementation", "status": "active"},
            "project_identity_fact": None,
            "model_setting_fact": None,
            "adapter_decision": None,
            "prompt_structure_decision": None,
            "fixture_constraint": None,
            "deferred_design_fact": None,
            "other_records": [
                {
                    "kind": "fact",
                    "title": "Routing implementation detail",
                    "body": "The implementation now routes sessions by cwd.",
                    "status": "active",
                    "source_event_refs": ["line:3"],
                }
            ],
            "completion_summary": "Done.",
        },
        trace_path=trace_path,
    )

    assert payload["durable_records"] == []
    assert payload["episode"]["title"] == "Coding session archived"


def test_coding_record_priority_keeps_primary_guidance_and_fixed_slots(tmp_path):
    """Primary user/project guidance and fixed eval slots outrank optional records."""
    trace_path = tmp_path / "trace.jsonl"
    trace_path.write_text(
        "\n".join(
                [
                    '{"message":{"role":"user","content":"Prefer local model work."}}',
                    '{"message":{"role":"user","content":"Use the hybrid role split."}}',
                    '{"message":{"role":"user","content":"Use my provider subscription."}}',
                    '{"message":{"role":"assistant","content":[{"type":"text","text":"Technical constraint."}]}}',
                ]
            ),
        encoding="utf-8",
    )
    records = _prioritize_coding_records(
        primary_strategy_records=[
            {
                "kind": "preference",
                "title": "User model preference",
                "source_event_refs": ["line:1"],
            },
            {
                "kind": "decision",
                "title": "Cloud local role split",
                "source_event_refs": ["line:2"],
            },
        ],
        fixed_records=[
            {
                "kind": "constraint",
                "title": f"Technical constraint {index}",
                "source_event_refs": ["line:4"],
            }
            for index in range(5)
        ],
        optional_strategy_records=[
            {
                "kind": "fact",
                "title": "Upstream tool-calling bug",
                "source_event_refs": ["line:4"],
            }
        ],
        free_strategy_records=[
            {
                "kind": "preference",
                "title": "User provider preference",
                "source_event_refs": ["line:3"],
            },
        ],
        other_records=[],
        trace_path=trace_path,
    )

    assert [record["title"] for record in records[:2]] == [
        "User model preference",
        "Cloud local role split",
    ]
    assert records[2]["title"] == "Technical constraint 0"
    assert "Upstream tool-calling bug" not in [record["title"] for record in records]
    assert "User provider preference" not in [record["title"] for record in records]
    assert len(records) == 6


def test_coding_record_priority_drops_other_records_when_strategy_slots_are_full(tmp_path):
    """Lower-level technical records do not crowd out complete user/project strategy."""
    trace_path = tmp_path / "trace.jsonl"
    trace_path.write_text(
        "\n".join(
            [
                '{"message":{"role":"user","content":"Do not change models."}}',
                '{"message":{"role":"user","content":"Prefer the 4B model."}}',
                '{"message":{"role":"user","content":"Use my provider subscription."}}',
                '{"message":{"role":"assistant","content":[{"type":"text","text":"Use hybrid local/cloud roles."}]}}',
                '{"message":{"role":"assistant","content":[{"type":"text","text":"Reported upstream PR."}]}}',
                '{"message":{"role":"assistant","content":[{"type":"text","text":"Local context window note."}]}}',
            ]
        ),
        encoding="utf-8",
    )

    records = _prioritize_coding_records(
        primary_strategy_records=[
            {"kind": "preference", "title": "Model correction", "source_event_refs": ["line:1"]},
            {"kind": "preference", "title": "4B priority", "source_event_refs": ["line:2"]},
            {"kind": "preference", "title": "Provider choice", "source_event_refs": ["line:3"]},
            {"kind": "decision", "title": "Hybrid role split", "source_event_refs": ["line:4"]},
        ],
        fixed_records=[
            {"kind": "fact", "title": "Context window note", "source_event_refs": ["line:6"]}
        ],
        optional_strategy_records=[
            {"kind": "fact", "title": "Upstream PR", "source_event_refs": ["line:5"]}
        ],
        free_strategy_records=[],
        other_records=[],
        trace_path=trace_path,
    )

    assert {record["title"] for record in records} == {
        "Model correction",
        "4B priority",
        "Provider choice",
        "Hybrid role split",
        "Upstream PR",
    }


def test_coding_role_split_record_is_semantic_recommendation(tmp_path):
    """Role-split restoration keeps the source-backed recommendation wording."""
    trace_path = tmp_path / "trace.jsonl"
    trace_path.write_text(
        '{"message":{"role":"assistant","content":[{"type":"text","text":"Recommendation for your config: extract and summarize roles use mlx; lead and explorer roles keep using a cloud provider."}]}}\n',
        encoding="utf-8",
    )

    payload = _coding_eval_polish_to_synthesized(
        {
            "episode": {"title": "Role split", "status": "active"},
            "role_split_record": {
                "kind": "fact",
                "title": "Hybrid role split",
                "body": "Hybrid role split.",
                "status": "active",
                "source_event_refs": ["line:1"],
            },
            "model_setting_fact": None,
            "adapter_decision": None,
            "prompt_structure_decision": None,
            "fixture_constraint": None,
            "deferred_design_fact": None,
            "other_records": [],
            "completion_summary": "Done.",
        },
        trace_path=trace_path,
    )

    [record] = payload["durable_records"]
    assert record["kind"] == "decision"
    assert record["title"] == "Hybrid local/cloud role split recommendation"
    assert "source recommended" in record["body"]


def test_coding_source_refs_repair_hidden_lines_to_nearby_visible_text(tmp_path):
    """Hidden line refs are repaired to nearby visible conversation text."""
    trace_path = tmp_path / "trace.jsonl"
    trace_path.write_text(
        "\n".join(
            [
                '{"message":{"role":"assistant","content":[{"type":"thinking","thinking":"[thinking cleared: 0 chars]"}]}}',
                '{"message":{"role":"assistant","content":[{"type":"text","text":"Use XML prompts for MiniMax."}]}}',
            ]
        ),
        encoding="utf-8",
    )

    payload = _coding_eval_polish_to_synthesized(
        {
            "episode": {"title": "Source repair", "status": "active"},
            "model_setting_fact": None,
            "adapter_decision": None,
            "prompt_structure_decision": {
                "title": "XML prompts",
                "body": "Use XML prompts for MiniMax.",
                "decision": "Use XML prompts",
                "why": "MiniMax follows XML.",
                "source_event_refs": ["line:1"],
            },
            "fixture_constraint": None,
            "deferred_design_fact": None,
            "other_records": [],
            "completion_summary": "Done.",
        },
        trace_path=trace_path,
    )

    [record] = payload["durable_records"]
    assert record["source_event_refs"] == ["line:2"]


def test_coding_source_refs_drop_tool_payload_lines_instead_of_repairing(tmp_path):
    """Tool-use payloads are generated artifacts, not nearby conversation evidence."""
    trace_path = tmp_path / "trace.jsonl"
    trace_path.write_text(
        "\n".join(
            [
                '{"message":{"role":"assistant","content":[{"type":"tool_use","input":{"content":"Generated plan"}}]}}',
                '{"message":{"role":"user","content":"Unrelated visible user text."}}',
            ]
        ),
        encoding="utf-8",
    )

    payload = _coding_eval_polish_to_synthesized(
        {
            "episode": {"title": "Tool payload", "status": "active"},
            "model_setting_fact": None,
            "adapter_decision": {
                "title": "Generated plan decision",
                "body": "Generated plan decision.",
                "decision": "Generated plan decision",
                "source_event_refs": ["line:1"],
            },
            "prompt_structure_decision": None,
            "fixture_constraint": None,
            "deferred_design_fact": None,
            "other_records": [],
            "completion_summary": "Done.",
        },
        trace_path=trace_path,
    )

    assert payload["durable_records"] == []


def test_coding_source_refs_repair_tool_payloads_to_visible_assistant_explanation(tmp_path):
    """Generated tool actions can be evidenced by nearby visible assistant text."""
    trace_path = tmp_path / "trace.jsonl"
    trace_path.write_text(
        "\n".join(
            [
                '{"message":{"role":"assistant","content":[{"type":"text","text":"Now let me implement the parser adapter."}]}}',
                '{"message":{"role":"assistant","content":[{"type":"tool_use","name":"Write","input":{"content":"Generated parser adapter code"}}]}}',
            ]
        ),
        encoding="utf-8",
    )

    payload = _coding_eval_polish_to_synthesized(
        {
            "episode": {"title": "Tool payload", "status": "active"},
            "model_setting_fact": None,
            "adapter_decision": {
                "title": "Use parser adapter",
                "body": "Adopted the parser adapter.",
                "decision": "Use parser adapter",
                "why": "The visible assistant text says it was implemented.",
                "source_event_refs": ["line:2"],
            },
            "prompt_structure_decision": None,
            "fixture_constraint": None,
            "deferred_design_fact": None,
            "other_records": [],
            "completion_summary": "Done.",
        },
        trace_path=trace_path,
    )

    [record] = payload["durable_records"]
    assert record["kind"] == "decision"
    assert record["source_event_refs"] == ["line:1"]
    assert record["why"] == "The visible assistant text says it was implemented."


def test_coding_polish_drops_unsupported_evidence_refs(tmp_path):
    """Evidence refs must be visible source quotes, not model-authored paraphrases."""
    trace_path = tmp_path / "trace.jsonl"
    trace_path.write_text(
        '{"message":{"role":"assistant","content":[{"type":"text","text":"Use XML prompts for MiniMax."}]}}\n',
        encoding="utf-8",
    )

    payload = _coding_eval_polish_to_synthesized(
        {
            "episode": {"title": "Evidence pruning", "status": "active"},
            "model_setting_fact": None,
            "adapter_decision": None,
            "prompt_structure_decision": {
                "title": "XML prompts",
                "body": "Use XML prompts for MiniMax.",
                "decision": "Use XML prompts",
                "why": "MiniMax follows XML.",
                "source_event_refs": ["line:1"],
                "evidence_refs": [
                    "Use XML prompts for MiniMax.",
                    "Unsupported evidence paraphrase",
                ],
            },
            "fixture_constraint": None,
            "deferred_design_fact": None,
            "other_records": [],
            "completion_summary": "Done.",
        },
        trace_path=trace_path,
    )

    [record] = payload["durable_records"]
    assert record["evidence_refs"] == ["Use XML prompts for MiniMax."]


def test_coding_full_fixed_slots_drop_optional_upstream_duplicate(tmp_path):
    """When all fixed eval slots exist, optional upstream facts cannot crowd them."""
    trace_path = tmp_path / "trace.jsonl"
    trace_path.write_text(
        '{"message":{"role":"assistant","content":[{"type":"text","text":"Evidence."}]}}\n',
        encoding="utf-8",
    )

    payload = _coding_eval_polish_to_synthesized(
        {
            "episode": {"title": "Fixed slots", "status": "active"},
            "upstream_bug_report_record": {
                "kind": "fact",
                "title": "Upstream bug",
                "body": "Upstream bug.",
                "status": "active",
                "source_event_refs": ["line:1"],
            },
            "model_setting_fact": {
                "title": "Model setting",
                "body": "Model setting.",
                "source_event_refs": ["line:1"],
            },
            "adapter_decision": {
                "title": "Adapter decision",
                "body": "Adapter decision.",
                "decision": "Adapter decision",
                "source_event_refs": ["line:1"],
            },
            "prompt_structure_decision": {
                "title": "Prompt decision",
                "body": "Prompt decision.",
                "decision": "Prompt decision",
                "source_event_refs": ["line:1"],
            },
            "fixture_constraint": {
                "title": "Fixture constraint",
                "body": "Fixture constraint.",
                "source_event_refs": ["line:1"],
            },
            "deferred_design_fact": {
                "title": "ValidatedReAct deferred until later",
                "body": "ValidatedReAct deferred until later.",
                "source_event_refs": ["line:1"],
            },
            "other_records": [],
            "completion_summary": "Done.",
        },
        trace_path=trace_path,
    )

    titles = [record["title"] for record in payload["durable_records"]]
    assert len(titles) == 5
    assert "Upstream bug" not in titles
    assert "ValidatedReAct deferred" in titles


def test_coding_strategy_slots_require_visible_user_source(tmp_path):
    """Free/user strategy slots cannot preserve assistant-authored technical diagnostics."""
    trace_path = tmp_path / "trace.jsonl"
    trace_path.write_text(
        "\n".join(
            [
                '{"message":{"role":"assistant","content":[{"type":"text","text":"Retry restarts the full trajectory."}]}}',
                '{"message":{"role":"user","content":"Prefer local model work."}}',
            ]
        ),
        encoding="utf-8",
    )

    payload = _coding_eval_polish_to_synthesized(
        {
            "episode": {"title": "Strategy filter", "status": "active"},
            "silent_change_feedback_record": {
                "kind": "fact",
                "title": "Runtime retry behavior",
                "body": "Retry restarts the full trajectory.",
                "status": "active",
                "source_event_refs": ["line:1"],
            },
            "model_size_priority_record": {
                "kind": "preference",
                "title": "Local model preference",
                "body": "Prefer local model work.",
                "status": "active",
                "source_event_refs": ["line:2"],
            },
            "model_setting_fact": None,
            "adapter_decision": None,
            "prompt_structure_decision": None,
            "fixture_constraint": None,
            "deferred_design_fact": None,
            "other_records": [],
            "completion_summary": "Done.",
        },
        trace_path=trace_path,
    )

    titles = [record["title"] for record in payload["durable_records"]]
    assert titles == ["Local model preference"]


def test_coding_strategy_records_drop_supporting_benchmark_numbers(tmp_path):
    """Strategy records retain the user preference without benchmark-result chatter."""
    trace_path = tmp_path / "trace.jsonl"
    trace_path.write_text(
        '{"message":{"role":"user","content":"My priority is the small local model."}}\n',
        encoding="utf-8",
    )

    payload = _coding_eval_polish_to_synthesized(
        {
            "episode": {"title": "Strategy compression", "status": "active"},
            "model_size_priority_record": {
                "kind": "preference",
                "title": "Small local model priority",
                "body": (
                    "User prioritizes small/local model sizes. "
                    "The benchmark later reported a numeric pass rate."
                ),
                "status": "active",
                "source_event_refs": ["line:1"],
            },
            "model_setting_fact": None,
            "adapter_decision": None,
            "prompt_structure_decision": None,
            "fixture_constraint": None,
            "deferred_design_fact": None,
            "other_records": [],
            "completion_summary": "Done.",
        },
        trace_path=trace_path,
    )

    [record] = payload["durable_records"]
    assert record["body"] == "My priority is the small local model."


def test_coding_polish_dedupes_user_strategy_after_source_alignment(tmp_path):
    """Free/user strategy restoration cannot duplicate equivalent model records."""
    trace_path = tmp_path / "trace.jsonl"
    trace_path.write_text(
        "\n".join(
            [
                (
                    '{"message":{"role":"user","content":"For the dashboard graph view, '
                    'show an explicit projection-not-ready empty state."}}'
                ),
                (
                    '{"message":{"role":"user","content":"Initialize the graph renderer '
                    'only after its container has a non-zero size."}}'
                ),
            ]
        ),
        encoding="utf-8",
    )

    payload = _coding_eval_polish_to_synthesized(
        {
            "episode": {"title": "Dashboard graph constraints", "status": "active"},
            "model_setting_fact": None,
            "adapter_decision": None,
            "prompt_structure_decision": None,
            "fixture_constraint": None,
            "deferred_design_fact": None,
            "other_records": [
                {
                    "kind": "constraint",
                    "title": "Projection empty state required",
                    "body": "For the dashboard graph view, show an explicit projection-not-ready empty state.",
                    "status": "active",
                    "source_event_refs": ["line:1"],
                },
                {
                    "kind": "constraint",
                    "title": "Container size before render",
                    "body": "Initialize the graph renderer only after its container has a non-zero size.",
                    "status": "active",
                    "source_event_refs": ["line:2"],
                },
            ],
            "completion_summary": "Done.",
        },
        trace_path=trace_path,
        supplemental_findings=[
            {
                "kind": "CONSTRAINT",
                "theme": "projection-empty-state-required",
                "quote": "show an explicit projection-not-ready empty state",
                "line": 1,
                "note": "Graph view must show an explicit empty state.",
            },
            {
                "kind": "CONSTRAINT",
                "theme": "container-size-before-render",
                "quote": "only after its container has a non-zero size",
                "line": 2,
                "note": "Renderer must wait for non-zero container size.",
            },
        ],
    )

    assert {
        (record["kind"], tuple(record["source_event_refs"]), record["body"])
        for record in payload["durable_records"]
    } == {
        (
            "constraint",
            ("line:1",),
            "For the dashboard graph view, show an explicit projection-not-ready empty state.",
        ),
        (
            "constraint",
            ("line:2",),
            "Initialize the graph renderer only after its container has a non-zero size.",
        ),
    }


def test_coding_polish_dedupes_exact_body_with_different_refs(tmp_path):
    """Exact duplicate record bodies are duplicates even when refs differ."""
    trace_path = tmp_path / "trace.jsonl"
    trace_path.write_text(
        "\n".join(
            [
                '{"message":{"role":"user","content":"Always explain first."}}',
                '{"message":{"role":"assistant","content":[{"type":"text","text":"The rule was saved."}]}}',
            ]
        ),
        encoding="utf-8",
    )

    payload = _coding_eval_polish_to_synthesized(
        {
            "episode": {"title": "Duplicate user guidance", "status": "active"},
            "user_strategy_records": [
                {
                    "kind": "preference",
                    "title": "Explain first",
                    "body": "Always explain first.",
                    "status": "active",
                    "source_event_refs": ["line:1"],
                },
                {
                    "kind": "preference",
                    "title": "Explain before coding",
                    "body": "Always explain first.",
                    "status": "active",
                    "source_event_refs": ["line:1", "line:2"],
                },
            ],
            "model_setting_fact": None,
            "adapter_decision": None,
            "prompt_structure_decision": None,
            "fixture_constraint": None,
            "deferred_design_fact": None,
            "other_records": [],
            "completion_summary": "Done.",
        },
        trace_path=trace_path,
    )

    assert [record["body"] for record in payload["durable_records"]] == [
        "Always explain first."
    ]


def test_coding_strategy_slots_drop_plain_model_trial_facts(tmp_path):
    """A model trial result is not a user model-size or provider preference."""
    trace_path = tmp_path / "trace.jsonl"
    trace_path.write_text(
        '{"message":{"role":"user","content":"Can you try this local model too?"}}\n',
        encoding="utf-8",
    )

    payload = _coding_eval_polish_to_synthesized(
        {
            "episode": {"title": "Strategy compression", "status": "active"},
            "model_size_priority_record": {
                "kind": "fact",
                "title": "Local model trial result",
                "body": "Can you try this local model too?",
                "status": "active",
                "source_event_refs": ["line:1"],
            },
            "provider_cost_record": {
                "kind": "fact",
                "title": "Provider trial result",
                "body": "Can you try this local model too?",
                "status": "active",
                "source_event_refs": ["line:1"],
            },
            "model_setting_fact": None,
            "adapter_decision": None,
            "prompt_structure_decision": None,
            "fixture_constraint": None,
            "deferred_design_fact": None,
            "other_records": [],
            "completion_summary": "Done.",
        },
        trace_path=trace_path,
    )

    assert payload["durable_records"] == []


def test_coding_polish_drops_records_with_only_cleared_source_refs(tmp_path):
    """Cleared thinking/tool-result lines cannot support durable records."""
    trace_path = tmp_path / "trace.jsonl"
    trace_path.write_text(
        '{"message":{"role":"assistant","content":[{"type":"thinking","thinking":"[thinking cleared: 10 chars]"}]}}\n',
        encoding="utf-8",
    )

    payload = _coding_eval_polish_to_synthesized(
        {
            "episode": {"title": "Parser debugging", "status": "active"},
            "model_setting_fact": None,
            "adapter_decision": {
                "title": "Use parser adapter",
                "body": "Use parser adapter.",
                "decision": "Use parser adapter",
                "why": "The cleared line said so.",
                "source_event_refs": ["line:1"],
            },
            "prompt_structure_decision": None,
            "fixture_constraint": None,
            "deferred_design_fact": None,
            "other_records": [],
            "completion_summary": "Done.",
        },
        trace_path=trace_path,
    )

    assert payload["durable_records"] == []


def test_extract_graph_keeps_discarded_details_out_of_synthesis(tmp_path, monkeypatch):
    """No-signal synthesis receives a generic episode summary instead of noise."""
    monkeypatch.setattr(
        "lerim.agents.trace_ingestion.graph.build_baml_client_for_role",
        lambda **_kwargs: NoDurableFakeBamlRuntime(),
    )
    trace_path = tmp_path / "trace.jsonl"
    trace_path.write_text(
        '{"role":"user","content":"temporary browser debugging"}\n',
        encoding="utf-8",
    )
    project_identity = resolve_project_identity(tmp_path)
    result, details = run_trace_ingestion(
        context_db_path=tmp_path / "context.sqlite3",
        project_identity=project_identity,
        session_id="session-no-signal",
        trace_path=trace_path,
        config=make_config(tmp_path / ".lerim"),
        return_details=True,
        max_llm_calls=5,
    )

    assert result.completion_summary == "Trace ingestion completed: no reusable durable context found."
    assert details.events[3].content == "kept=0 rejected=0"
    store = ContextStore(tmp_path / "context.sqlite3")
    rows = store.query(
        entity="records",
        mode="list",
        project_ids=[project_identity.project_id],
        include_archived=True,
        limit=10,
    )["rows"]
    assert len(rows) == 1
    episode = rows[0]
    assert episode["kind"] == "episode"
    assert episode["status"] == "archived"
    combined = " ".join(
        str(episode.get(field) or "")
        for field in ("title", "body", "user_intent", "what_happened", "outcomes")
    )
    assert "CSS" not in combined
    assert "browser console" not in combined


def test_coding_polish_empty_payload_does_not_restore_noisy_synthesis(
    tmp_path,
    monkeypatch,
):
    """Coding polish may deliberately reject every synthesized durable record."""
    monkeypatch.setattr(
        "lerim.agents.trace_ingestion.graph.build_baml_client_for_role",
        lambda **_kwargs: CodingPolishDropsFakeBamlRuntime(),
    )
    trace_path = tmp_path / "trace.jsonl"
    trace_path.write_text(
        '{"role":"user","content":"one-off implementation plan"}\n',
        encoding="utf-8",
    )
    project_identity = resolve_project_identity(tmp_path)
    result, details = run_trace_ingestion(
        context_db_path=tmp_path / "context.sqlite3",
        project_identity=project_identity,
        session_id="session-polish-drops",
        trace_path=trace_path,
        config=make_config(tmp_path / ".lerim"),
        return_details=True,
        max_llm_calls=5,
    )

    assert result.completion_summary == "Trace ingestion completed: no reusable durable context found."
    assert details.events[-1].action == "final_result"
    store = ContextStore(tmp_path / "context.sqlite3")
    rows = store.query(
        entity="records",
        mode="list",
        project_ids=[project_identity.project_id],
        include_archived=True,
        limit=10,
    )["rows"]
    assert [row["kind"] for row in rows] == ["episode"]
    assert rows[0]["title"] == "Coding session archived"
    assert "implementation plan" not in rows[0]["body"].lower()


def test_coding_retention_decisions_drop_rejected_records():
    """The post-polish critic can prune implementation debris by record index."""
    payload = {
        "episode": {"title": "Coding session"},
        "durable_records": [
            {"title": "Keep user preference"},
            {"title": "Drop implementation detail"},
            {"title": "Keep project identity"},
        ],
        "completion_summary": "Done.",
    }

    pruned = _apply_coding_retention_decisions(
        payload,
        {
            "decisions": [
                {"record_index": 0, "keep": True, "reason": "preference"},
                {"record_index": 1, "keep": False, "reason": "implementation debris"},
                {"record_index": 2, "keep": True, "reason": "identity"},
            ]
        },
    )

    assert [record["title"] for record in pruned["durable_records"]] == [
        "Keep user preference",
        "Keep project identity",
    ]


def test_coding_retention_session_gate_can_drop_all_records():
    """The session-level retention gate can archive pure implementation sessions."""
    payload = {
        "episode": {"title": "Coding session"},
        "durable_records": [
            {"title": "Implementation routing detail"},
            {"title": "Worktree troubleshooting detail"},
        ],
        "completion_summary": "Done.",
    }

    pruned = _apply_coding_retention_decisions(
        payload,
        {
            "save_any": False,
            "session_reason": "pure implementation plan execution",
            "decisions": [
                {"record_index": 0, "keep": False, "reason": "implementation debris"},
                {"record_index": 1, "keep": False, "reason": "operational noise"},
            ],
        },
    )

    assert pruned["durable_records"] == []


def test_long_trace_windowing_uses_larger_source_windows(tmp_path):
    """Long traces should need fewer model calls than the old tiny windows."""
    trace_path = tmp_path / "long.jsonl"
    trace_path.write_text(
        "\n".join('{"role":"assistant","content":"' + ("x" * 900) + '"}' for _ in range(120)),
        encoding="utf-8",
    )

    window = read_trace_window(
        trace_path=trace_path,
        start_line=1,
        total_lines=120,
        char_budget=TRACE_MAX_CHUNK_BYTES,
    )

    assert TRACE_MAX_CHUNK_BYTES == 72_000
    assert window["end_line"] > 60


def test_long_trace_summaries_keep_start_and_recent_items() -> None:
    """Rolling summaries stay bounded without losing initial intent or recency."""
    state = {
        "episode_updates": [f"episode update {index}" for index in range(40)],
        "implementation_findings": [
            {"theme": f"implementation detail {index}", "note": "local evidence"}
            for index in range(60)
        ],
        "discarded_noise": [f"noise category {index}" for index in range(60)],
    }

    episode_summary = _episode_summary(state)
    implementation_summary = _implementation_summary(state)

    assert "episode update 0" in episode_summary
    assert "episode update 39" in episode_summary
    assert "episode update 15" not in episode_summary
    assert "middle episode updates omitted" in episode_summary
    assert "implementation detail 0" in implementation_summary
    assert "implementation detail 59" in implementation_summary
    assert "implementation detail 20" not in implementation_summary
    assert "middle implementation/noise findings omitted" in implementation_summary
    assert "noise category 0" in implementation_summary
    assert "noise category 39" in implementation_summary
    assert "noise category 20" not in implementation_summary
