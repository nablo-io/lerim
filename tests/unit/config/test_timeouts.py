"""Tests for shared timeout contracts."""

from __future__ import annotations

from pathlib import Path

from lerim.config.timeouts import (
    ANSWER_REQUEST_TIMEOUT_SECONDS,
    HTTP_API_POST_TIMEOUT_SECONDS,
)
from lerim.server.cli_api_client import API_POST_TIMEOUT_SECONDS

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_answer_timeout_is_five_minutes() -> None:
    """The answer endpoint timeout is the user-visible five-minute ceiling."""
    assert ANSWER_REQUEST_TIMEOUT_SECONDS == 300


def test_cli_timeout_allows_server_answer_deadline() -> None:
    """The CLI waits long enough for the server to return its answer timeout."""
    assert API_POST_TIMEOUT_SECONDS == HTTP_API_POST_TIMEOUT_SECONDS
    assert API_POST_TIMEOUT_SECONDS > ANSWER_REQUEST_TIMEOUT_SECONDS
