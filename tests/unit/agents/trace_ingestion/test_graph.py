"""Tests for the BAML-backed extract graph."""

from __future__ import annotations

from lerim.agents.trace_ingestion.api import run_trace_ingestion
from lerim.context import ContextStore, resolve_project_identity
from tests.helpers import make_config


class FakeBamlRuntime:
    """BAML client double that exercises scan, filter, and synthesize phases."""

    def __init__(self) -> None:
        """Track the filtered summary passed into synthesis."""
        self.synthesis_input = ""

    def ObserveSourceWindow(self, **_kwargs):
        """Return one strong candidate and one source-local candidate."""
        return {
            "episode_update": "The source session evaluated extraction filtering.",
            "durable_findings": [
                {
                    "theme": "general source filtering",
                    "level": "decision",
                    "note": "Extraction should filter durable signal before synthesis.",
                },
                {
                    "theme": "local command output",
                    "level": "fact",
                    "note": "A one-run command produced a local output.",
                },
            ],
            "implementation_findings": [
                {
                    "theme": "command output",
                    "level": "implementation",
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
                    "level": "decision",
                    "note": "Extraction should filter durable signal before synthesis.",
                }
            ],
            "rejected_findings": [
                {
                    "theme": "local command output",
                    "level": "fact",
                    "note": "A one-run command produced a local output.",
                }
            ],
            "filtering_summary": "Kept reusable extraction policy and rejected local evidence.",
        }

    def SynthesizeContextRecords(self, **kwargs):
        """Assert synthesis sees the filtered candidates only."""
        self.synthesis_input = kwargs["durable_findings_summary"]
        assert "general trace filtering" in self.synthesis_input
        assert "local command output" not in self.synthesis_input
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
            "record_updates": [],
        }

    def ReviewSynthesizedContextRecords(self, **kwargs):
        """Return the synthesized payload unchanged after final review."""
        assert "general trace filtering" in kwargs["durable_findings_summary"]
        assert "local command output" not in kwargs["durable_findings_summary"]
        return {
            "completion_summary": "Extraction completed.",
            "episode": {
                "title": "Trace filtering extraction",
                "body": "The trace was scanned, filtered, reviewed, and persisted.",
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
            "record_updates": [],
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
        max_llm_calls=4,
    )

    assert result.completion_summary == "Extraction completed."
    assert [event.action for event in details.events] == [
        "resolve_scope",
        "read_window",
        "scan_window",
        "filter_signals",
        "synthesize_records",
        "review_records",
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
