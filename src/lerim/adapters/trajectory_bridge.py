"""Subprocess client for the Letta ``@letta-ai/trajectory`` normalizer bridge.

Lerim parses native harness transcripts with Letta's trajectory-v1 normalizer
instead of hand-rolled adapters. That normalizer ships on npm only, so it is
installed under the Lerim data dir and driven through the protocol-v1 bridge
script it ships (``dist/python-cli.js``): one JSON request document on stdin,
one JSON response document on stdout.

    request : {"version": 1, "requests": [<list|normalize|checkpoint>, ...]}
    response: {"version": 1, "results": [{"ok": true, "result": {...}}
                                         | {"ok": false, "error": {...}}, ...]}

Many requests share one node process, and a failed request never fails its
batch: per-request outcomes are returned as :class:`BridgeResult` values so a
caller can skip-and-log an unparseable session and keep the rest. Runtime
failures (node missing, install broken, malformed response) raise instead.

This module owns the transport only. Interpreting a normalize payload into
trajectory-v1 records, secret redaction, and cache writing belong to the caller.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Any

from lerim.config.settings import get_global_data_dir_path

PROTOCOL_VERSION = 1
TRAJECTORY_PACKAGE = "@letta-ai/trajectory"
TRAJECTORY_VERSION = "0.2.0"
MINIMUM_NODE_MAJOR = 20

# Sources the pinned package accepts. Verified against the bridge itself, which
# rejects anything else with `unknown_source`. Notably there is no `pi` adapter.
SUPPORTED_SOURCES = frozenset(
    {
        "claude-code",
        "codex",
        "deepagents",
        "hermes",
        "letta-code",
        "openclaw",
        "openhands",
    }
)

# One batch crosses the node stdin pipe as a single JSON document, so the whole
# batch is materialized three times over (Python str, UTF-8 bytes, node string)
# before a record is produced. An unbounded batch therefore scales memory with
# corpus size rather than session size. 32 MiB keeps peak usage bounded while
# still amortizing process spawn across ~45 typical 700 KB sessions. A single
# transcript larger than the budget is never split: it becomes a batch of one.
MAX_BATCH_PAYLOAD_BYTES = 32 * 1024 * 1024

_NPM_INSTALL_TIMEOUT_SECONDS = 300
_STDERR_EXCERPT_CHARS = 2000

_NODE_INSTALL_HINT = (
    f"node >= {MINIMUM_NODE_MAJOR} is required to parse agent transcripts. "
    "Install it (macOS: `brew install node`, Linux: https://nodejs.org/en/download) "
    "and re-run."
)


class TrajectoryErrorCode(StrEnum):
    """Per-request failure codes the trajectory bridge can return.

    Mirrors upstream ``NormalizationErrorCode`` plus ``internal_error``, which
    the bridge script emits for any non-``NormalizationError`` throw.
    """

    INVALID_INPUT = "invalid_input"
    UNKNOWN_SOURCE = "unknown_source"
    PYTHON_UNAVAILABLE = "python_unavailable"
    PYTHON_DEPENDENCY_MISSING = "python_dependency_missing"
    CHECKPOINT_DATABASE_NOT_FOUND = "checkpoint_database_not_found"
    CHECKPOINT_DATABASE_UNREADABLE = "checkpoint_database_unreadable"
    CHECKPOINT_READ_FAILED = "checkpoint_read_failed"
    CHECKPOINT_NOT_FOUND = "checkpoint_not_found"
    CHECKPOINT_MESSAGES_MISSING = "checkpoint_messages_missing"
    INVALID_CHECKPOINT_STATE = "invalid_checkpoint_state"
    LISTING_UNAVAILABLE = "listing_unavailable"
    MISSING_USER_RECORDS = "missing_user_records"
    MISSING_ASSISTANT_RECORDS = "missing_assistant_records"
    INVALID_NORMALIZED_TRANSCRIPT = "invalid_normalized_transcript"
    SOURCE_GROUP_REQUIRED = "source_group_required"
    SOURCE_GROUP_CONFLICT = "source_group_conflict"
    INTERNAL_ERROR = "internal_error"


class TrajectoryBridgeError(RuntimeError):
    """Base class for every trajectory bridge failure."""


class NodeUnavailableError(TrajectoryBridgeError):
    """Node is missing from PATH or older than the required major version."""


class BridgeInstallError(TrajectoryBridgeError):
    """The pinned trajectory npm package could not be installed or verified."""


class BridgeExecutionError(TrajectoryBridgeError):
    """The node bridge process could not run or exited non-zero: no results."""


class BridgeProtocolError(TrajectoryBridgeError):
    """The bridge response did not match protocol v1."""


@dataclass(frozen=True, slots=True)
class BridgeError:
    """A single failed request's typed error payload."""

    name: str
    code: TrajectoryErrorCode
    message: str


class TrajectoryRequestError(TrajectoryBridgeError):
    """One request inside a batch failed; the other requests are unaffected.

    Raised only by :meth:`BridgeResult.unwrap`. Callers that ingest whole
    corpora should inspect ``BridgeResult.ok`` and skip-and-log instead.
    """

    def __init__(self, error: BridgeError) -> None:
        """Wrap a per-request bridge error, exposing its code for dispatch."""
        super().__init__(f"{error.code}: {error.message}")
        self.error = error
        self.code = error.code


@dataclass(frozen=True, slots=True)
class BridgeResult:
    """One request's outcome, positionally aligned with the request list.

    Exactly one of ``result`` and ``error`` is set, discriminated by ``ok``.
    ``result`` is the raw protocol payload: ``{"items", "nextCursor"?}`` for a
    list request, ``{"records", "diagnostics"}`` for a normalize request.
    """

    ok: bool
    result: dict[str, Any] | None
    error: BridgeError | None

    def unwrap(self) -> dict[str, Any]:
        """Return the payload, raising :class:`TrajectoryRequestError` on failure."""
        if self.error is not None:
            raise TrajectoryRequestError(self.error)
        if self.result is None:
            raise BridgeProtocolError("bridge reported success without a result payload")
        return self.result


@lru_cache(maxsize=1)
def resolve_node() -> Path:
    """Return the node executable, requiring at least the supported major version."""
    node_path = shutil.which("node")
    if not node_path:
        raise NodeUnavailableError(f"node not found on PATH. {_NODE_INSTALL_HINT}")
    # A `node` on PATH is not a runnable node: version-manager shims for removed
    # runtimes and wrong-architecture binaries fail to exec, and `subprocess.run`
    # reports that as OSError rather than a returncode.
    try:
        completed = subprocess.run(
            [node_path, "--version"], capture_output=True, text=True, check=False
        )
    except OSError as exc:
        raise NodeUnavailableError(
            f"`{node_path} --version` could not be executed: {exc}. {_NODE_INSTALL_HINT}"
        ) from exc
    if completed.returncode != 0:
        raise NodeUnavailableError(
            f"`{node_path} --version` failed: {completed.stderr.strip()}. "
            f"{_NODE_INSTALL_HINT}"
        )
    reported = completed.stdout.strip()
    major_text = reported.removeprefix("v").split(".", 1)[0]
    if not major_text.isdigit():
        raise NodeUnavailableError(
            f"unreadable node version {reported!r}. {_NODE_INSTALL_HINT}"
        )
    if int(major_text) < MINIMUM_NODE_MAJOR:
        raise NodeUnavailableError(
            f"node {reported} is too old. {_NODE_INSTALL_HINT}"
        )
    return Path(node_path)


def node_root() -> Path:
    """Return the Lerim-managed npm prefix holding the trajectory package."""
    return get_global_data_dir_path() / "node"


def bridge_script_path() -> Path:
    """Return the path of the installed protocol-v1 bridge script."""
    return (
        node_root() / "node_modules" / "@letta-ai" / "trajectory" / "dist" / "python-cli.js"
    )


def installed_trajectory_version() -> str | None:
    """Return the installed trajectory package version, or None when absent."""
    manifest = node_root() / "node_modules" / "@letta-ai" / "trajectory" / "package.json"
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    version = payload.get("version") if isinstance(payload, dict) else None
    return version if isinstance(version, str) else None


def ensure_trajectory_installed() -> Path:
    """Install the pinned trajectory npm package when needed and return the bridge path.

    A no-op once the bridge script exists at the pinned version, so it is cheap
    to call before every batch and safe to call from ``lerim init``.
    """
    script = bridge_script_path()
    if script.is_file() and installed_trajectory_version() == TRAJECTORY_VERSION:
        return script

    npm_path = shutil.which("npm")
    if not npm_path:
        raise BridgeInstallError(
            "npm not found on PATH, so "
            f"{TRAJECTORY_PACKAGE}@{TRAJECTORY_VERSION} cannot be installed. "
            f"{_NODE_INSTALL_HINT}"
        )
    root = node_root()
    try:
        root.mkdir(parents=True, exist_ok=True)
        completed = subprocess.run(
            [
                npm_path,
                "install",
                "--prefix",
                str(root),
                "--no-fund",
                "--no-audit",
                f"{TRAJECTORY_PACKAGE}@{TRAJECTORY_VERSION}",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=_NPM_INSTALL_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise BridgeInstallError(
            f"npm install {TRAJECTORY_PACKAGE}@{TRAJECTORY_VERSION} timed out after "
            f"{_NPM_INSTALL_TIMEOUT_SECONDS}s"
        ) from exc
    except OSError as exc:
        # An npm that cannot be executed (broken shim, wrong architecture) or a
        # prefix directory that cannot be created is an install failure, not a
        # per-session data problem, so it must not surface as a bare OSError.
        raise BridgeInstallError(
            f"npm install {TRAJECTORY_PACKAGE}@{TRAJECTORY_VERSION} into {root} "
            f"could not be executed: {exc}. {_NODE_INSTALL_HINT}"
        ) from exc
    if completed.returncode != 0:
        raise BridgeInstallError(
            f"npm install {TRAJECTORY_PACKAGE}@{TRAJECTORY_VERSION} failed into {root}: "
            f"{completed.stderr.strip()[:_STDERR_EXCERPT_CHARS]}"
        )

    installed = installed_trajectory_version()
    if not script.is_file() or installed != TRAJECTORY_VERSION:
        raise BridgeInstallError(
            f"npm install completed but {script} is missing or the installed version "
            f"is {installed!r} instead of {TRAJECTORY_VERSION!r}"
        )
    return script


def batch_requests(
    requests: Sequence[Mapping[str, Any]],
    *,
    max_payload_bytes: int = MAX_BATCH_PAYLOAD_BYTES,
) -> list[list[Mapping[str, Any]]]:
    """Split requests into batches bounded by serialized payload size.

    Order is preserved. A single request larger than the budget is emitted as
    its own batch rather than dropped or split, because a transcript is only
    normalizable whole.
    """
    if max_payload_bytes < 1:
        raise ValueError("max_payload_bytes must be >= 1")
    batches: list[list[Mapping[str, Any]]] = []
    current: list[Mapping[str, Any]] = []
    current_bytes = 0
    for request in requests:
        size = len(json.dumps(request, ensure_ascii=False).encode("utf-8"))
        if current and current_bytes + size > max_payload_bytes:
            batches.append(current)
            current = []
            current_bytes = 0
        current.append(request)
        current_bytes += size
    if current:
        batches.append(current)
    return batches


def run(requests: Sequence[Mapping[str, Any]]) -> list[BridgeResult]:
    """Execute one batch of bridge requests and return per-request outcomes.

    Results are positionally aligned with ``requests``. Per-request failures are
    returned, never raised, so one bad session cannot fail its batch.
    """
    if not requests:
        return []
    node_path = resolve_node()
    script = ensure_trajectory_installed()
    payload = json.dumps(
        {"version": PROTOCOL_VERSION, "requests": list(requests)}, ensure_ascii=False
    ).encode("utf-8")
    try:
        completed = subprocess.run(
            [str(node_path), str(script)], input=payload, capture_output=True, check=False
        )
    except OSError as exc:
        # `resolve_node` is cached for the process, so a node that disappeared or
        # became unexecutable since then fails here instead.
        raise BridgeExecutionError(
            f"trajectory bridge could not be executed with {node_path}: {exc}. "
            f"{_NODE_INSTALL_HINT}"
        ) from exc
    if completed.returncode != 0:
        stderr = completed.stderr.decode("utf-8", errors="replace").strip()
        raise BridgeExecutionError(
            f"trajectory bridge exited {completed.returncode}: "
            f"{stderr[:_STDERR_EXCERPT_CHARS]}"
        )
    return _parse_response(completed.stdout, expected=len(requests))


def _parse_response(stdout: bytes, *, expected: int) -> list[BridgeResult]:
    """Parse and validate a protocol-v1 response document into typed results."""
    try:
        response = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise BridgeProtocolError(
            f"trajectory bridge produced non-JSON output: {stdout[:200]!r}"
        ) from exc
    if not isinstance(response, dict):
        raise BridgeProtocolError("trajectory bridge response must be an object")
    if response.get("version") != PROTOCOL_VERSION:
        raise BridgeProtocolError(
            f"trajectory bridge protocol version {response.get('version')!r} "
            f"is not the supported version {PROTOCOL_VERSION}"
        )
    results = response.get("results")
    if not isinstance(results, list):
        raise BridgeProtocolError("trajectory bridge response is missing a results list")
    if len(results) != expected:
        raise BridgeProtocolError(
            f"trajectory bridge returned {len(results)} results for {expected} requests"
        )
    return [_parse_result(item) for item in results]


def _parse_result(item: Any) -> BridgeResult:
    """Convert one raw protocol result entry into a typed :class:`BridgeResult`."""
    if not isinstance(item, dict):
        raise BridgeProtocolError(f"trajectory bridge result must be an object: {item!r}")
    if item.get("ok") is True:
        result = item.get("result")
        if not isinstance(result, dict):
            raise BridgeProtocolError(
                f"successful bridge result must carry an object payload: {item!r}"
            )
        return BridgeResult(ok=True, result=result, error=None)
    if item.get("ok") is not False:
        raise BridgeProtocolError(f"bridge result is missing a boolean ok flag: {item!r}")
    error = item.get("error")
    if not isinstance(error, dict):
        raise BridgeProtocolError(
            f"failed bridge result must carry an error object: {item!r}"
        )
    raw_code = error.get("code")
    try:
        code = TrajectoryErrorCode(raw_code)
    except ValueError as exc:
        raise BridgeProtocolError(
            f"unknown trajectory error code {raw_code!r}; the installed package does "
            f"not match the pinned version {TRAJECTORY_VERSION}"
        ) from exc
    return BridgeResult(
        ok=False,
        result=None,
        error=BridgeError(
            name=str(error.get("name") or "Error"),
            code=code,
            message=str(error.get("message") or ""),
        ),
    )


if __name__ == "__main__":
    """Run a real-path smoke test against the local claude-code session store."""
    print(f"node: {resolve_node()}")
    print(f"bridge: {ensure_trajectory_installed()}")

    listing = run([{"list": {"source": "claude-code", "limit": 3}}])[0].unwrap()
    items = listing["items"]
    print(f"listed {len(items)} claude-code sessions")

    normalize = [
        {"source": "claude-code", "transcript": Path(item["path"]).read_text(encoding="utf-8")}
        for item in items
    ]
    batches = batch_requests(normalize)
    print(f"{len(normalize)} normalize requests -> {len(batches)} batch(es)")
    for batch in batches:
        for outcome in run(batch):
            if not outcome.ok:
                assert outcome.error is not None
                print(f"  skipped ({outcome.error.code})")
                continue
            payload = outcome.unwrap()
            assert payload["records"][0]["role"] == "meta"
            print(
                f"  {len(payload['records'])} records, "
                f"{len(payload['diagnostics'])} diagnostics"
            )
