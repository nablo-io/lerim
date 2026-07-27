"""Unit tests for the connected platform registry.

The registry is now a thin map over :mod:`lerim.adapters.trajectory_source`,
so what matters is that it never advertises a platform Lerim cannot ingest and
never silently drops one it can — including for entries left in an existing
``platforms.json`` by a previous release.
"""

from __future__ import annotations

import json

import pytest

from lerim.adapters.registry import (
    KNOWN_PLATFORMS,
    auto_seed,
    connect_platform,
    default_path_for,
    get_connected_agents,
    get_connected_platform_paths,
    list_platforms,
    load_platforms,
    remove_platform,
    save_platforms,
)
from lerim.adapters.trajectory_source import SOURCE_MAP, UNSUPPORTED_PLATFORMS
from tests.trajectory_helpers import write_claude_session


def test_known_platforms_covers_supported_and_dropped_ones():
    """`lerim connect <name>` recognizes every name Lerim has ever supported."""
    assert set(KNOWN_PLATFORMS) == set(SOURCE_MAP) | set(UNSUPPORTED_PLATFORMS)


@pytest.mark.parametrize("platform", sorted(SOURCE_MAP))
def test_a_supported_platform_has_a_default_store_path(platform):
    """Auto-detection needs somewhere to look for each supported harness."""
    assert default_path_for(platform) is not None


@pytest.mark.parametrize("platform", sorted(UNSUPPORTED_PLATFORMS))
def test_a_dropped_platform_advertises_no_path(platform):
    """A platform Lerim cannot read must not be offered by the init wizard."""
    assert default_path_for(platform) is None


def test_an_unknown_platform_has_no_default_path():
    """A typo resolves to nothing rather than to some other harness's store."""
    assert default_path_for("unknown_platform_xyz") is None


@pytest.mark.parametrize("platform", sorted(UNSUPPORTED_PLATFORMS))
def test_connecting_a_dropped_platform_reports_why_and_stores_nothing(
    platform, tmp_path
):
    """Connecting cursor/opencode/pi explains the regression instead of succeeding."""
    registry = tmp_path / "platforms.json"

    result = connect_platform(registry, platform)

    assert result["status"] == "unsupported_platform"
    assert result["message"] == UNSUPPORTED_PLATFORMS[platform]
    assert not registry.exists()


def test_connecting_a_missing_path_is_reported_without_writing_state(tmp_path):
    """A path that does not exist is a user error, not a half-connected platform."""
    registry = tmp_path / "platforms.json"

    result = connect_platform(registry, "claude", custom_path=str(tmp_path / "nope"))

    assert result["status"] == "path_not_found"
    assert not registry.exists()


def test_connecting_a_platform_counts_its_sessions(tmp_path, trajectory_data_root):
    """A successful connect reports how many sessions the store actually holds."""
    registry = tmp_path / "platforms.json"
    root = tmp_path / "claude-projects"
    write_claude_session(root, "sess-one")
    write_claude_session(root, "sess-two")

    result = connect_platform(registry, "claude", custom_path=str(root))

    assert result["status"] == "connected"
    assert result["session_count"] == 2
    assert load_platforms(registry)["platforms"]["claude"]["path"] == str(root.resolve())


def test_registry_state_round_trips_through_disk(tmp_path):
    """What is saved is what is loaded, so a restart keeps its connections."""
    registry = tmp_path / "platforms.json"
    data = {
        "platforms": {"claude": {"path": "/tmp/claude", "connected_at": "2026-01-01"}}
    }

    save_platforms(registry, data)

    assert load_platforms(registry) == data


@pytest.mark.parametrize("payload", ["not json at all", "[1,2,3]", '{"platforms":5}'])
def test_a_corrupt_registry_reads_as_no_platforms(tmp_path, payload):
    """A damaged registry file degrades to empty rather than crashing startup."""
    registry = tmp_path / "platforms.json"
    registry.write_text(payload, encoding="utf-8")

    assert load_platforms(registry) == {"platforms": {}}


def test_removing_a_platform_is_reported_and_idempotent(tmp_path):
    """Disconnecting twice is not an error the second time."""
    registry = tmp_path / "platforms.json"
    save_platforms(
        registry, {"platforms": {"claude": {"path": "/tmp", "connected_at": "x"}}}
    )

    assert remove_platform(registry, "claude") is True
    assert remove_platform(registry, "claude") is False
    assert load_platforms(registry)["platforms"] == {}


def test_listing_counts_sessions_per_connected_platform(tmp_path, trajectory_data_root):
    """The dashboard's platform list reports live counts from the store."""
    registry = tmp_path / "platforms.json"
    root = tmp_path / "claude-projects"
    write_claude_session(root, "sess-one")
    save_platforms(
        registry, {"platforms": {"claude": {"path": str(root), "connected_at": "x"}}}
    )

    entries = list_platforms(registry, with_counts=True)

    assert [entry["name"] for entry in entries] == ["claude"]
    assert entries[0]["session_count"] == 1
    assert entries[0]["exists"] is True


def test_a_leftover_dropped_platform_lists_with_its_reason(tmp_path):
    """An upgrade must not crash on a cursor entry written by an older release."""
    registry = tmp_path / "platforms.json"
    save_platforms(
        registry,
        {
            "platforms": {
                "cursor": {"path": str(tmp_path), "connected_at": "2026-01-01"},
                "opencode": {"path": str(tmp_path), "connected_at": "2026-01-01"},
            }
        },
    )

    entries = {entry["name"]: entry for entry in list_platforms(registry)}

    assert entries["cursor"]["status"] == "unsupported_platform"
    assert entries["cursor"]["message"] == UNSUPPORTED_PLATFORMS["cursor"]
    assert entries["cursor"]["session_count"] == 0


def test_ingest_excludes_dropped_platforms_left_in_the_registry(tmp_path):
    """A stale cursor entry is listed for the user but never handed to ingest."""
    registry = tmp_path / "platforms.json"
    save_platforms(
        registry,
        {
            "platforms": {
                "claude": {"path": str(tmp_path), "connected_at": "x"},
                "cursor": {"path": str(tmp_path), "connected_at": "x"},
            }
        },
    )

    assert get_connected_agents(registry) == ["claude"]
    assert set(get_connected_platform_paths(registry)) == {"claude"}


def test_connected_paths_skip_stores_that_are_gone(tmp_path):
    """A platform whose directory was deleted is not offered to the normalizer."""
    registry = tmp_path / "platforms.json"
    save_platforms(
        registry,
        {
            "platforms": {
                "claude": {"path": str(tmp_path / "deleted"), "connected_at": "x"}
            }
        },
    )

    assert get_connected_platform_paths(registry) == {}


def test_auto_seed_connects_only_platforms_present_on_disk(tmp_path, monkeypatch):
    """First run picks up installed harnesses and ignores the rest."""
    registry = tmp_path / "platforms.json"
    installed = tmp_path / "claude-projects"
    installed.mkdir()
    monkeypatch.setattr(
        "lerim.adapters.registry.default_root",
        lambda name: installed if name == "claude" else tmp_path / "absent",
    )

    data = auto_seed(registry)

    assert set(data["platforms"]) == {"claude"}
    assert json.loads(registry.read_text(encoding="utf-8"))["platforms"]["claude"][
        "path"
    ] == str(installed)


def test_auto_seed_does_not_overwrite_an_existing_registry(tmp_path):
    """Seeding is a first-run action, so it never rewrites a user's choices."""
    registry = tmp_path / "platforms.json"
    save_platforms(
        registry, {"platforms": {"codex": {"path": "/tmp/codex", "connected_at": "x"}}}
    )

    assert auto_seed(registry)["platforms"] == {
        "codex": {"path": "/tmp/codex", "connected_at": "x"}
    }
