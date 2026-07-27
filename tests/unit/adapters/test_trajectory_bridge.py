"""Unit tests for the trajectory normalizer bridge transport.

These drive the real ``node`` bridge wherever the behavior under test is the
protocol itself. Failure paths that need a hostile peer — no node, an old node,
a malformed response — install a stub executable on ``PATH`` instead of patching
Lerim internals, so the code under test runs unchanged.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from lerim.adapters import trajectory_bridge
from lerim.adapters.trajectory_bridge import (
    MAX_BATCH_PAYLOAD_BYTES,
    PROTOCOL_VERSION,
    SUPPORTED_SOURCES,
    TRAJECTORY_VERSION,
    BridgeExecutionError,
    BridgeInstallError,
    BridgeProtocolError,
    BridgeResult,
    NodeUnavailableError,
    TrajectoryErrorCode,
    TrajectoryRequestError,
    batch_requests,
    bridge_script_path,
    ensure_trajectory_installed,
    installed_trajectory_version,
    resolve_node,
    run,
)
from tests.unit.adapters.conftest import TRAJECTORY_FIXTURES_DIR

# A claude-code transcript whose only turn is the user's. Upstream rejects it
# with `missing_assistant_records`; 11 of 12 real local sessions look like this.
ABANDONED_TRANSCRIPT = json.dumps(
    {
        "type": "user",
        "uuid": "u1",
        "timestamp": "2026-03-06T14:15:22.394Z",
        "message": {"role": "user", "content": "start something"},
    }
)


def _install_stub_node(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, version: str, body: str
) -> Path:
    """Put a stub ``node`` on PATH that reports ``version`` and otherwise runs ``body``."""
    bin_dir = tmp_path / "stub-bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    stub = bin_dir / "node"
    stub.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "--version" ]; then\n'
        f"  echo {version}\n"
        "  exit 0\n"
        "fi\n"
        f"{body}\n",
        encoding="utf-8",
    )
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ["PATH"])
    resolve_node.cache_clear()
    return stub


@pytest.fixture(autouse=True)
def _reset_node_cache():
    """Keep the process-wide node lookup cache from leaking between tests."""
    resolve_node.cache_clear()
    yield
    resolve_node.cache_clear()


# --------------------------------------------------------------------------
# Batching
# --------------------------------------------------------------------------


def test_batch_requests_keeps_requests_in_one_batch_under_budget():
    """Requests that fit the budget travel as a single node invocation."""
    requests = [{"source": "claude-code", "transcript": "x" * 100} for _ in range(5)]
    assert batch_requests(requests) == [requests]


def test_batch_requests_splits_on_payload_budget_preserving_order():
    """A batch is cut when adding the next request would exceed the budget."""
    requests = [{"source": "claude-code", "transcript": "x" * 400} for _ in range(4)]

    batches = batch_requests(requests, max_payload_bytes=900)

    assert [len(batch) for batch in batches] == [2, 2]
    assert [request for batch in batches for request in batch] == requests


def test_batch_requests_emits_oversized_request_as_its_own_batch():
    """A transcript larger than the budget is normalized whole, never split."""
    small = {"source": "codex", "transcript": "x"}
    huge = {"source": "codex", "transcript": "x" * 5_000}

    batches = batch_requests([small, huge, small], max_payload_bytes=1_000)

    assert [len(batch) for batch in batches] == [1, 1, 1]
    assert batches[1] == [huge]


def test_batch_requests_rejects_a_non_positive_budget():
    """A budget below one byte cannot produce batches, so it fails fast."""
    with pytest.raises(ValueError, match="max_payload_bytes"):
        batch_requests([{"source": "codex", "transcript": "x"}], max_payload_bytes=0)


def test_default_batch_budget_is_bounded():
    """The shipped budget is finite, so peak memory does not scale with corpus size."""
    assert 0 < MAX_BATCH_PAYLOAD_BYTES <= 64 * 1024 * 1024


# --------------------------------------------------------------------------
# Install and node resolution
# --------------------------------------------------------------------------


def test_run_with_no_requests_returns_no_results_without_touching_node(
    tmp_path, monkeypatch
):
    """An empty request list short-circuits before any subprocess is spawned."""
    monkeypatch.setenv("PATH", str(tmp_path))
    resolve_node.cache_clear()

    assert run([]) == []


def test_resolve_node_reports_a_missing_node_with_an_install_hint(tmp_path, monkeypatch):
    """Node absent from PATH fails fast and names how to install it."""
    monkeypatch.setenv("PATH", str(tmp_path))
    resolve_node.cache_clear()

    with pytest.raises(NodeUnavailableError, match="node not found on PATH"):
        resolve_node()


def test_resolve_node_rejects_a_node_older_than_the_minimum(tmp_path, monkeypatch):
    """A node below the required major version is refused, not used anyway."""
    _install_stub_node(tmp_path, monkeypatch, version="v18.20.4", body="exit 0")

    with pytest.raises(NodeUnavailableError, match="v18.20.4 is too old"):
        resolve_node()


def test_resolve_node_rejects_an_unreadable_version_string(tmp_path, monkeypatch):
    """A shim that answers `--version` with nonsense is not assumed to be new enough."""
    _install_stub_node(tmp_path, monkeypatch, version="banana", body="exit 0")

    with pytest.raises(NodeUnavailableError, match="unreadable node version"):
        resolve_node()


def test_resolve_node_accepts_the_real_node_on_this_machine():
    """The developer/CI node satisfies the pin, so the rest of the suite is meaningful."""
    assert resolve_node().exists()


def test_installed_package_matches_the_pinned_version(trajectory_data_root):
    """The bootstrap installs exactly the pinned version, and only that."""
    script = ensure_trajectory_installed()

    assert script == bridge_script_path()
    assert script.is_file()
    assert installed_trajectory_version() == TRAJECTORY_VERSION


def test_ensure_trajectory_installed_is_idempotent(trajectory_data_root):
    """Calling the bootstrap twice does not reinstall or change the resolved path."""
    first = ensure_trajectory_installed()
    stamp = first.stat().st_mtime_ns

    assert ensure_trajectory_installed() == first
    assert first.stat().st_mtime_ns == stamp


def test_missing_npm_is_reported_as_an_install_failure(tmp_path, monkeypatch):
    """With no npm to bootstrap from, the bridge says so instead of raising OSError."""
    stub = _install_stub_node(tmp_path, monkeypatch, version="v22.0.0", body="exit 0")
    monkeypatch.setenv("PATH", str(stub.parent))
    monkeypatch.setattr(
        trajectory_bridge, "node_root", lambda: tmp_path / "empty-node-root"
    )

    with pytest.raises(BridgeInstallError, match="npm not found on PATH"):
        ensure_trajectory_installed()


# --------------------------------------------------------------------------
# Protocol
# --------------------------------------------------------------------------


def test_list_request_returns_transcript_metadata_without_parsing(
    tmp_path, trajectory_data_root
):
    """A list request reports id/path/updatedAt/sizeBytes for each transcript."""
    project = tmp_path / "projects" / "-workspace-project"
    project.mkdir(parents=True)
    transcript = TRAJECTORY_FIXTURES_DIR / "claude-code" / "tool-call" / "input.jsonl"
    (project / "sess-a.jsonl").write_bytes(transcript.read_bytes())

    payload = run(
        [{"list": {"source": "claude-code", "root": str(tmp_path / "projects")}}]
    )[0].unwrap()

    assert [item["id"] for item in payload["items"]] == ["sess-a"]
    item = payload["items"][0]
    assert item["sizeBytes"] == transcript.stat().st_size
    assert item["updatedAt"]


def test_one_failed_request_does_not_fail_its_batch(trajectory_data_root):
    """A rejected session is returned as an error while its neighbours normalize.

    This is the tolerance requirement in transport form: 11 of 12 real local
    sessions are abandoned transcripts, so a batch that aborted on the first
    rejection would ingest almost nothing.
    """
    good = (
        TRAJECTORY_FIXTURES_DIR / "claude-code" / "tool-call" / "input.jsonl"
    ).read_text(encoding="utf-8")

    results = run(
        [
            {"source": "claude-code", "transcript": good},
            {"source": "claude-code", "transcript": ABANDONED_TRANSCRIPT},
            {"source": "claude-code", "transcript": good},
        ]
    )

    assert [result.ok for result in results] == [True, False, True]
    assert results[1].error is not None
    assert results[1].error.code is TrajectoryErrorCode.MISSING_ASSISTANT_RECORDS
    assert results[0].unwrap()["records"][0]["role"] == "meta"
    assert results[2].unwrap()["records"][0]["role"] == "meta"


def test_unwrap_raises_a_typed_error_carrying_the_upstream_code(trajectory_data_root):
    """A caller that wants the exception form gets the code, not a bare message."""
    result = run(
        [{"source": "claude-code", "transcript": ABANDONED_TRANSCRIPT}]
    )[0]

    with pytest.raises(TrajectoryRequestError) as excinfo:
        result.unwrap()
    assert excinfo.value.code is TrajectoryErrorCode.MISSING_ASSISTANT_RECORDS


def test_unsupported_source_is_reported_as_unknown_source(trajectory_data_root):
    """Sources with no upstream adapter fail with a typed code, not a crash."""
    result = run([{"source": "cursor", "transcript": "{}"}])[0]

    assert result.error is not None
    assert result.error.code is TrajectoryErrorCode.UNKNOWN_SOURCE


def test_supported_sources_match_what_the_bridge_accepts(trajectory_data_root):
    """SUPPORTED_SOURCES stays in step with what the installed package normalizes.

    The bridge names its own sources in the `unknown_source` message, so an
    upgrade that adds one fails here rather than silently leaving Lerim's set
    stale. ``deepagents`` is the one declared source that message never lists:
    it is read through the separate checkpoint request, not from a transcript.
    """
    result = run([{"source": "definitely-not-a-source", "transcript": "{}"}])[0]

    assert result.error is not None
    reported = {
        token.strip().rstrip(".")
        for token in result.error.message.split(":")[-1].split(",")
    }
    assert reported <= set(SUPPORTED_SOURCES)
    assert set(SUPPORTED_SOURCES) - reported == {"deepagents"}


def test_results_are_positionally_aligned_with_requests(trajectory_data_root):
    """Result N belongs to request N, which is how callers rejoin their listings."""
    cases = ["claude-code/tool-call", "codex/tool-calls", "openclaw/tool-calls"]
    requests = [
        {
            "source": case.split("/")[0],
            "transcript": (TRAJECTORY_FIXTURES_DIR / case / "input.jsonl").read_text(
                encoding="utf-8"
            ),
        }
        for case in cases
    ]

    results = run(requests)

    assert [result.unwrap()["records"][0]["source"] for result in results] == [
        "claude-code",
        "codex",
        "openclaw",
    ]


def test_tool_arguments_bounds_are_honoured(trajectory_data_root):
    """The bounds Lerim sends actually truncate, and the truncation is reported."""
    transcript = json.dumps(
        {
            "type": "assistant",
            "uuid": "a1",
            "timestamp": "2026-03-06T14:15:30.000Z",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"c": "ls"}}
                ],
            },
        }
    )
    tool_result = json.dumps(
        {
            "type": "user",
            "uuid": "u2",
            "timestamp": "2026-03-06T14:15:31.000Z",
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "t1",
                        "content": [{"type": "text", "text": "A" * 5_000}],
                    }
                ],
            },
        }
    )
    user = json.dumps(
        {
            "type": "user",
            "uuid": "u1",
            "timestamp": "2026-03-06T14:15:22.394Z",
            "message": {"role": "user", "content": "list files"},
        }
    )

    payload = run(
        [
            {
                "source": "claude-code",
                "transcript": "\n".join([user, transcript, tool_result]),
                "bounds": {
                    "toolResults": {"maxCharacters": 100, "strategy": "head-tail"}
                },
            }
        ]
    )[0].unwrap()

    tool_records = [r for r in payload["records"] if r["role"] == "tool"]
    assert len(tool_records[0]["content"]) < 5_000
    assert any(
        item["code"] == "tool_result_truncated" for item in payload["diagnostics"]
    )


# --------------------------------------------------------------------------
# Malformed peers
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("stdout", "match"),
    [
        ("not json at all", "non-JSON output"),
        ('[1,2,3]', "must be an object"),
        ('{"version":99,"results":[]}', "protocol version"),
        ('{"version":1}', "missing a results list"),
        ('{"version":1,"results":[]}', "0 results for 1 requests"),
        ('{"version":1,"results":[{"ok":true}]}', "must carry an object payload"),
        ('{"version":1,"results":[{}]}', "missing a boolean ok flag"),
        ('{"version":1,"results":[{"ok":false}]}', "must carry an error object"),
        (
            '{"version":1,"results":[{"ok":false,"error":{"code":"brand_new_code"}}]}',
            "unknown trajectory error code",
        ),
    ],
)
def test_malformed_bridge_responses_raise_protocol_errors(
    tmp_path, monkeypatch, stdout, match
):
    """Any response that is not protocol v1 is rejected instead of half-read."""
    _install_stub_node(
        tmp_path,
        monkeypatch,
        version="v22.0.0",
        body=f"cat > /dev/null\nprintf '%s' '{stdout}'\nexit 0",
    )
    monkeypatch.setattr(trajectory_bridge, "ensure_trajectory_installed", lambda: tmp_path)

    with pytest.raises(BridgeProtocolError, match=match):
        run([{"source": "claude-code", "transcript": "{}"}])


def test_a_bridge_that_exits_non_zero_raises_with_its_stderr(tmp_path, monkeypatch):
    """A crashed bridge is an execution failure carrying the peer's own message."""
    _install_stub_node(
        tmp_path,
        monkeypatch,
        version="v22.0.0",
        body="cat > /dev/null\necho 'heap out of memory' >&2\nexit 7",
    )
    monkeypatch.setattr(trajectory_bridge, "ensure_trajectory_installed", lambda: tmp_path)

    with pytest.raises(BridgeExecutionError, match="exited 7: heap out of memory"):
        run([{"source": "claude-code", "transcript": "{}"}])


def test_a_node_that_cannot_be_executed_is_not_leaked_as_oserror(tmp_path, monkeypatch):
    """A node that resolves but fails to exec is a typed bridge failure."""
    _install_stub_node(tmp_path, monkeypatch, version="v22.0.0", body="exit 0")
    monkeypatch.setattr(trajectory_bridge, "ensure_trajectory_installed", lambda: tmp_path)
    monkeypatch.setattr(
        trajectory_bridge, "resolve_node", lambda: tmp_path / "gone" / "node"
    )

    with pytest.raises(BridgeExecutionError, match="could not be executed"):
        run([{"source": "claude-code", "transcript": "{}"}])


def test_bridge_result_reports_success_without_a_payload_as_a_protocol_error():
    """`ok` and a payload are one fact; disagreeing is a protocol violation."""
    with pytest.raises(BridgeProtocolError, match="without a result payload"):
        BridgeResult(ok=True, result=None, error=None).unwrap()


def test_protocol_version_is_the_one_the_bridge_speaks(tmp_path, trajectory_data_root):
    """The version Lerim sends is the version the installed bridge answers with."""
    assert PROTOCOL_VERSION == 1
    assert run([{"list": {"source": "claude-code", "root": str(tmp_path)}}])[0].ok
