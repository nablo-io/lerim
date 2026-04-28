"""Adapter cache path defaults."""

from __future__ import annotations

from lerim.adapters import claude, codex, cursor, opencode
from tests.helpers import write_test_config


def test_adapter_trace_caches_live_under_cache_traces(tmp_path, monkeypatch):
    """Compacted traces are grouped by agent under the active data root."""
    config_path = write_test_config(tmp_path)
    monkeypatch.setenv("LERIM_CONFIG", str(config_path))

    assert claude._default_cache_dir() == tmp_path / "cache" / "traces" / "claude"
    assert codex._default_cache_dir() == tmp_path / "cache" / "traces" / "codex"
    assert cursor._default_cache_dir() == tmp_path / "cache" / "traces" / "cursor"
    assert (
        opencode._default_cache_dir()
        == tmp_path / "cache" / "traces" / "opencode"
    )
