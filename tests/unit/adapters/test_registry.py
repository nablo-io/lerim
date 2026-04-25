"""Unit tests for the connected platform registry."""

from __future__ import annotations


from lerim.adapters.registry import (
    auto_seed,
    connect_platform,
    default_path_for,
    get_adapter,
    get_connected_agents,
    get_connected_platform_paths,
    list_platforms,
    load_platforms,
    remove_platform,
    save_platforms,
)


def test_get_adapter_known_platform():
    """get_adapter('claude') returns claude adapter module."""
    adapter = get_adapter("claude")
    assert adapter is not None
    assert hasattr(adapter, "default_path")
    assert hasattr(adapter, "count_sessions")
    assert hasattr(adapter, "iter_sessions")


def test_get_adapter_unknown_platform():
    """get_adapter('unknown') returns None."""
    adapter = get_adapter("unknown_platform_xyz")
    assert adapter is None


def test_default_path_for_known_platform():
    """default_path_for returns adapter default path for known platforms."""
    result = default_path_for("claude")
    assert result is not None
    assert result.name == "projects"


def test_default_path_for_unknown_platform():
    """default_path_for returns None for unknown platforms."""
    assert default_path_for("unknown_platform_xyz") is None


def test_load_save_platforms_roundtrip(tmp_path):
    """save_platforms then load_platforms -> identical data."""
    path = tmp_path / "platforms.json"
    data = {
        "platforms": {"claude": {"path": "/tmp/claude", "connected_at": "2026-01-01"}}
    }
    save_platforms(path, data)
    loaded = load_platforms(path)
    assert loaded["platforms"]["claude"]["path"] == "/tmp/claude"


def test_connect_platform(tmp_path):
    """connect_platform adds entry to registry JSON."""
    reg_path = tmp_path / "platforms.json"
    # Create a fake traces directory
    traces = tmp_path / "traces"
    traces.mkdir()
    result = connect_platform(reg_path, "claude", custom_path=str(traces))
    assert result["status"] == "connected"
    assert result["name"] == "claude"
    # Verify it was saved
    loaded = load_platforms(reg_path)
    assert "claude" in loaded["platforms"]


def test_remove_platform(tmp_path):
    """remove_platform removes entry from registry JSON."""
    reg_path = tmp_path / "platforms.json"
    data = {"platforms": {"claude": {"path": "/tmp", "connected_at": "2026-01-01"}}}
    save_platforms(reg_path, data)
    assert remove_platform(reg_path, "claude") is True
    loaded = load_platforms(reg_path)
    assert "claude" not in loaded["platforms"]
    # Removing non-existent returns False
    assert remove_platform(reg_path, "nonexistent") is False


def test_list_platforms_with_counts(tmp_path):
    """list_platforms with_counts=True includes session counts."""
    reg_path = tmp_path / "platforms.json"
    traces = tmp_path / "traces"
    traces.mkdir()
    # Write a non-empty JSONL so count > 0 for claude
    (traces / "sess.jsonl").write_text('{"type":"user"}\n', encoding="utf-8")
    data = {
        "platforms": {"claude": {"path": str(traces), "connected_at": "2026-01-01"}}
    }
    save_platforms(reg_path, data)
    platforms = list_platforms(reg_path, with_counts=True)
    assert len(platforms) == 1
    assert platforms[0]["name"] == "claude"
    assert "session_count" in platforms[0]
    assert "exists" in platforms[0]


def test_auto_seed_detects_installed(tmp_path, monkeypatch):
    """auto_seed connects platforms that exist on disk."""
    reg_path = tmp_path / "platforms.json"
    fake_claude = tmp_path / "claude_traces"
    fake_claude.mkdir()
    # Monkeypatch the default_path for claude to point to our fake dir
    import lerim.adapters.claude as claude_mod

    monkeypatch.setattr(claude_mod, "default_path", lambda: fake_claude)
    data = auto_seed(reg_path)
    assert "claude" in data.get("platforms", {})


def test_get_connected_agents(tmp_path):
    """get_connected_agents returns list of connected agent names."""
    reg_path = tmp_path / "platforms.json"
    data = {"platforms": {"codex": {"path": "/tmp", "connected_at": "2026-01-01"}}}
    save_platforms(reg_path, data)
    agents = get_connected_agents(reg_path)
    assert "codex" in agents


def test_get_connected_platform_paths(tmp_path):
    """get_connected_platform_paths returns name->path map."""
    reg_path = tmp_path / "platforms.json"
    traces = tmp_path / "traces"
    traces.mkdir()
    data = {
        "platforms": {"claude": {"path": str(traces), "connected_at": "2026-01-01"}}
    }
    save_platforms(reg_path, data)
    paths = get_connected_platform_paths(reg_path)
    assert "claude" in paths
    assert paths["claude"] == traces
