"""Ingest routing: which stores get scanned, and as which agent type.

There is one normalizer now, so what varies per platform is the store root it
is pointed at and the ``source`` it is asked for. These tests run the real
normalizer over real transcripts in real store layouts, because a routing bug
is exactly the kind of thing a session double cannot show: a fake adapter
returns the agent type it was told to return no matter which path was scanned.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lerim.adapters import trajectory_bridge
from lerim.adapters.trajectory_source import UNSUPPORTED_PLATFORMS
from lerim.config import settings as config_settings
from lerim.sessions import catalog
from lerim.sessions.catalog import fetch_session_doc, get_indexed_run_ids
from tests.helpers import write_test_config
from tests.trajectory_helpers import (
    write_abandoned_session,
    write_claude_session,
    write_codex_session,
)


@pytest.fixture
def ingest_env(tmp_path, monkeypatch):
    """Return a Lerim data root in ``tmp_path`` that still reaches the node install.

    The whole point of these tests is that ingest reads real stores, so only the
    npm install is shared with the developer machine; the sessions DB, the trace
    caches and the platform registry are all per-test.
    """
    real_node_root = trajectory_bridge.node_root()
    monkeypatch.setattr(trajectory_bridge, "node_root", lambda: real_node_root)

    def _configure(*, connected: dict[str, Path] | None = None, **sections) -> None:
        """Point the config and the platform registry at the given stores."""
        monkeypatch.setenv(
            "LERIM_CONFIG", str(write_test_config(tmp_path, **sections))
        )
        config_settings.reload_config()
        catalog.init_sessions_db()
        paths = dict(connected or {})
        monkeypatch.setattr(
            catalog.adapter_registry, "get_connected_platform_paths", lambda _p: paths
        )
        monkeypatch.setattr(
            catalog.adapter_registry, "get_connected_agents", lambda _p: sorted(paths)
        )

    yield _configure
    config_settings.load_config.cache_clear()


def test_only_connected_stores_are_scanned(ingest_env, tmp_path) -> None:
    """A harness installed on the machine but not connected stays out of the index."""
    connected = tmp_path / "claude-projects"
    unconnected = tmp_path / "codex-sessions"
    write_claude_session(connected, "connected-run")
    write_codex_session(unconnected, "unconnected-run")
    ingest_env(connected={"claude": connected})

    details = catalog.index_new_sessions(return_details=True)

    assert [session.run_id for session in details] == ["connected-run"]
    assert fetch_session_doc("unconnected-run") is None


def test_each_store_is_indexed_under_its_own_agent_type(ingest_env, tmp_path) -> None:
    """Two connected platforms are both scanned, each row typed by its store."""
    claude_root = tmp_path / "claude-projects"
    codex_root = tmp_path / "codex-sessions"
    write_claude_session(claude_root, "claude-run")
    write_codex_session(codex_root, "codex-run")
    ingest_env(connected={"claude": claude_root, "codex": codex_root})

    catalog.index_new_sessions(return_details=True)

    assert fetch_session_doc("claude-run")["agent_type"] == "claude"
    assert fetch_session_doc("codex-run")["agent_type"] == "codex"
    assert get_indexed_run_ids() >= {"claude-run", "codex-run"}


def test_index_new_sessions_can_be_limited_to_one_agent(ingest_env, tmp_path) -> None:
    """``lerim index --agent claude`` must not walk the codex store as well."""
    claude_root = tmp_path / "claude-projects"
    codex_root = tmp_path / "codex-sessions"
    write_claude_session(claude_root, "claude-run")
    write_codex_session(codex_root, "codex-run")
    ingest_env(connected={"claude": claude_root, "codex": codex_root})

    details = catalog.index_new_sessions(agents=["claude"], return_details=True)

    assert [session.run_id for session in details] == ["claude-run"]
    assert fetch_session_doc("codex-run") is None


def test_a_connected_store_that_is_gone_does_not_stop_the_others(
    ingest_env, tmp_path
) -> None:
    """A deleted harness directory is a normal machine state, not an ingest failure."""
    claude_root = tmp_path / "claude-projects"
    write_claude_session(claude_root, "claude-run")
    ingest_env(
        connected={"claude": claude_root, "codex": tmp_path / "deleted-codex-store"}
    )

    details = catalog.index_new_sessions(return_details=True)

    assert [session.run_id for session in details] == ["claude-run"]


@pytest.mark.parametrize("platform", sorted(UNSUPPORTED_PLATFORMS))
def test_a_dropped_platform_left_in_the_registry_is_never_scanned(
    platform, ingest_env, tmp_path
) -> None:
    """cursor/opencode/pi have no trajectory adapter, so ingest must skip them.

    A registry written by an older release still names them. Handing one to the
    normalizer would ask for a source the package does not have; the routing
    layer has to drop it before that happens.
    """
    claude_root = tmp_path / "claude-projects"
    write_claude_session(claude_root, "claude-run")
    ingest_env(connected={"claude": claude_root, platform: tmp_path / "stale-store"})

    details = catalog.index_new_sessions(return_details=True)

    assert [session.run_id for session in details] == ["claude-run"]


def test_a_rejected_transcript_does_not_stop_its_store(ingest_env, tmp_path) -> None:
    """One abandoned session in a store never costs that store its other sessions."""
    claude_root = tmp_path / "claude-projects"
    write_claude_session(claude_root, "good-run")
    write_abandoned_session(claude_root, "abandoned-run")
    ingest_env(connected={"claude": claude_root})

    details = catalog.index_new_sessions(return_details=True)

    assert [session.run_id for session in details] == ["good-run"]
    assert fetch_session_doc("abandoned-run") is None


def test_index_new_sessions_indexes_custom_project_folder(ingest_env, tmp_path) -> None:
    """Custom projects are scanned directly, with no connected platform at all."""
    traces = tmp_path / "clean-traces"
    traces.mkdir()
    trace_file = traces / "support-run.jsonl"
    trace_file.write_text(
        "\n".join(
            [
                json.dumps({"role": "meta", "source": "support-agent"}),
                json.dumps(
                    {
                        "role": "user",
                        "content": "Support agent approved escalation.",
                        "timestamp": "2026-05-16T09:00:00Z",
                    }
                ),
                json.dumps(
                    {
                        "role": "assistant",
                        "content": "Escalation approved and logged.",
                        "timestamp": "2026-05-16T09:01:00Z",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    ingest_env(
        projects={"support": str(traces)},
        project_types={"support": "custom"},
    )

    details = catalog.index_new_sessions(return_details=True)

    assert len(details) == 1
    assert details[0].agent_type == "custom"
    assert details[0].repo_path == str(traces.resolve())
    assert details[0].session_path == str(trace_file.resolve())
    assert fetch_session_doc(details[0].run_id)["agent_type"] == "custom"


def test_custom_folders_and_connected_stores_are_both_indexed(
    ingest_env, tmp_path
) -> None:
    """A user with both a harness and a clean-trace folder gets both, not one."""
    claude_root = tmp_path / "claude-projects"
    write_claude_session(claude_root, "claude-run")
    traces = tmp_path / "clean-traces"
    traces.mkdir()
    (traces / "support-run.jsonl").write_text(
        "\n".join(
            [
                json.dumps({"role": "meta", "source": "support-agent"}),
                json.dumps(
                    {
                        "role": "user",
                        "content": "escalate this ticket",
                        "timestamp": "2026-05-16T09:00:00Z",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    ingest_env(
        connected={"claude": claude_root},
        projects={"support": str(traces)},
        project_types={"support": "custom"},
    )

    details = catalog.index_new_sessions(return_details=True)

    assert {session.agent_type for session in details} == {"claude", "custom"}


def test_a_normalized_session_is_cached_as_valid_trajectory_v1(
    ingest_env, tmp_path, assert_valid_trace_file
) -> None:
    """Ingest through the catalog writes the same cache contract as the adapter path.

    ``read_trace_window`` numbers this file's lines and extracted records cite
    them as ``line:<N>``, so the format is checked wherever a file is produced,
    not only where the writer is called directly.
    """
    claude_root = tmp_path / "claude-projects"
    write_claude_session(claude_root, "cached-run")
    ingest_env(connected={"claude": claude_root})

    catalog.index_new_sessions(return_details=True)

    cache_path = (
        Path(config_settings.get_trace_cache_dir("claude")) / "cached-run.jsonl"
    )
    records = assert_valid_trace_file(cache_path)
    assert records[0]["source"] == "claude-code"
