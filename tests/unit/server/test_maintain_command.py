"""CLI maintain-command behavior tests."""

from __future__ import annotations

import pytest

from lerim.server import cli
from tests.helpers import run_cli_json


def test_maintain_forwards_to_api(monkeypatch: pytest.MonkeyPatch) -> None:
    """Maintain command forwards to HTTP API and returns result."""
    fake_response = {"status": "started", "job_id": "abc", "dry_run": True}
    monkeypatch.setattr(cli, "_api_post", lambda _path, _body: fake_response)
    code, payload = run_cli_json(["maintain", "--dry-run", "--json"])
    assert code == 0
    assert payload["dry_run"] is True
