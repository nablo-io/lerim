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
        max_llm_calls=3,
    )

    assert result.completion_summary == "Trace ingestion completed: 1 durable record created."
    assert [event.action for event in details.events] == [
        "resolve_scope",
        "read_window",
        "scan_window",
        "filter_signals",
        "synthesize_records",
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
        max_llm_calls=3,
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
