"""Integration-style cloud sync cases for shipper state and pull behavior."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from lerim.cloud.shipper import _ShipperState, _pull_records, _ship_records
from lerim.context import ContextStore, resolve_project_identity
from tests.helpers import make_config
from tests.integration.cloud.helpers import load_cloud_expectation


def _make_cloud_config(base: Path, *, projects: dict[str, Path]):
    """Build a config with cloud sync enabled for the selected projects."""
    return replace(
        make_config(base),
        cloud_token="tok-test",
        projects={name: str(path) for name, path in projects.items()},
    )


def _project_id_for(project_path: Path) -> str:
    """Resolve the canonical project id for one test project path."""
    return resolve_project_identity(project_path).project_id


@pytest.fixture
def inline_cloud_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run shipper thread offloads inline so tests stay deterministic."""

    async def _inline_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr("lerim.cloud.shipper.asyncio.to_thread", _inline_to_thread)


@pytest.fixture
def stable_git_roots(monkeypatch: pytest.MonkeyPatch) -> None:
    """Resolve each temp project path as its own git root."""

    def _identity_git_root(repo_path: Path | None = None) -> Path:
        return Path(repo_path or ".").expanduser().resolve()

    monkeypatch.setattr("lerim.context.project_identity.git_root_for", _identity_git_root)


@pytest.mark.integration
def test_pull_records_normalizes_legacy_kinds_to_fact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    inline_cloud_calls: None,
    stable_git_roots: None,
) -> None:
    """Pull should map legacy cloud kinds onto canonical fact records."""
    expectation = load_cloud_expectation("pull_records_normalizes_legacy_kinds_to_fact")["expected"]
    project_root = tmp_path / "projects" / "alpha"
    project_root.mkdir(parents=True)
    config = _make_cloud_config(tmp_path, projects={"alpha": project_root})
    state = _ShipperState()

    monkeypatch.setattr(
        "lerim.cloud.shipper._get_json_sync",
        lambda *args, **kwargs: {
            "records": [
                {
                    "record_id": "cloud-project",
                    "record_kind": "project",
                    "title": "Freeze window",
                    "body": "A project-style cloud record should normalize.",
                    "status": "active",
                    "project": "alpha",
                    "cloud_edited_at": "2026-04-20T10:00:00Z",
                },
                {
                    "record_id": "cloud-learning",
                    "record_kind": "learning",
                    "title": "Observed lesson",
                    "body": "A learning-style cloud record should normalize.",
                    "status": "active",
                    "project": "alpha",
                    "cloud_edited_at": "2026-04-20T10:01:00Z",
                },
                {
                    "record_id": "cloud-feedback",
                    "record_kind": "feedback",
                    "title": "Style feedback",
                    "body": "A feedback-style cloud record should normalize.",
                    "status": "active",
                    "project": "alpha",
                    "cloud_edited_at": "2026-04-20T10:02:00Z",
                },
                {
                    "record_id": "cloud-implementation",
                    "record_kind": "implementation",
                    "title": "Implementation note",
                    "body": "An implementation-style cloud record should normalize.",
                    "status": "active",
                    "project": "alpha",
                    "cloud_edited_at": "2026-04-20T10:03:00Z",
                },
            ]
        },
    )

    pulled = asyncio.run(_pull_records("https://api.test", "tok-test", config, state))

    assert pulled == 4
    assert state.records_pulled_at == "2026-04-20T10:03:00Z"

    store = ContextStore(config.context_db_path)
    project_id = _project_id_for(project_root)
    for record_id in (
        "cloud-project",
        "cloud-learning",
        "cloud-feedback",
        "cloud-implementation",
    ):
        record = store.fetch_record(record_id, project_ids=[project_id], include_versions=True)
        assert record is not None
        assert record["kind"] == expectation["normalized_kind"]
        assert len(record["versions"]) == 1
        assert record["versions"][0]["change_kind"] == expectation["version_change_kind"]


@pytest.mark.integration
def test_pull_records_updates_existing_record_and_appends_version(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    inline_cloud_calls: None,
    stable_git_roots: None,
) -> None:
    """Pull should update an existing record in place and add a new version row."""
    expectation = load_cloud_expectation("pull_records_updates_existing_record_and_appends_version")["expected"]
    project_root = tmp_path / "projects" / "alpha"
    project_root.mkdir(parents=True)
    config = _make_cloud_config(tmp_path, projects={"alpha": project_root})
    state = _ShipperState()
    store = ContextStore(config.context_db_path)
    store.initialize()

    project_id = _project_id_for(project_root)
    store.register_project(resolve_project_identity(project_root))
    store.create_record(
        project_id=project_id,
        session_id=None,
        record_id="cloud-existing",
        kind="fact",
        title="Original title",
        body="Original body",
        valid_from="2026-03-01T00:00:00Z",
    )

    monkeypatch.setattr(
        "lerim.cloud.shipper._get_json_sync",
        lambda *args, **kwargs: {
            "records": [
                {
                    "record_id": "cloud-existing",
                    "record_kind": "decision",
                    "title": "Prefer typed sync contracts",
                    "body": "They keep cloud pull and local reads aligned.",
                    "status": "active",
                    "project": "alpha",
                    "cloud_edited_at": "2026-04-20T11:00:00Z",
                }
            ]
        },
    )

    pulled = asyncio.run(_pull_records("https://api.test", "tok-test", config, state))

    assert pulled == 1
    assert state.records_pulled_at == "2026-04-20T11:00:00Z"

    record = store.fetch_record("cloud-existing", project_ids=[project_id], include_versions=True)
    assert record is not None
    assert record["kind"] == expectation["kind"]
    assert record["title"] == "Prefer typed sync contracts"
    assert record["body"] == "They keep cloud pull and local reads aligned."
    assert record["decision"] == "Prefer typed sync contracts"
    assert record["why"] == "They keep cloud pull and local reads aligned."
    assert record["valid_from"] == "2026-03-01T00:00:00Z"
    assert len(record["versions"]) == int(expectation["version_count"])
    assert record["versions"][0]["version_no"] == 2
    assert record["versions"][0]["change_kind"] == expectation["latest_change_kind"]
    assert record["versions"][1]["version_no"] == 1


@pytest.mark.integration
def test_pull_records_preserves_named_project_scope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    inline_cloud_calls: None,
    stable_git_roots: None,
) -> None:
    """Pull should write the record into the project named by the cloud payload."""
    expectation = load_cloud_expectation("pull_records_preserves_named_project_scope")["expected"]
    alpha_root = tmp_path / "projects" / "alpha"
    beta_root = tmp_path / "projects" / "beta"
    alpha_root.mkdir(parents=True)
    beta_root.mkdir(parents=True)
    config = _make_cloud_config(tmp_path, projects={"alpha": alpha_root, "beta": beta_root})
    state = _ShipperState()

    monkeypatch.setattr(
        "lerim.cloud.shipper._get_json_sync",
        lambda *args, **kwargs: {
            "records": [
                {
                    "record_id": "cloud-beta-only",
                    "record_kind": "fact",
                    "title": "Scoped to beta",
                    "body": "This record must land in beta, not alpha.",
                    "status": "active",
                    "project": "beta",
                    "cloud_edited_at": "2026-04-20T12:00:00Z",
                }
            ]
        },
    )

    pulled = asyncio.run(_pull_records("https://api.test", "tok-test", config, state))

    assert pulled == 1

    store = ContextStore(config.context_db_path)
    alpha_id = _project_id_for(alpha_root)
    beta_id = _project_id_for(beta_root)
    beta_record = store.fetch_record(
        "cloud-beta-only",
        project_ids=[beta_id],
        include_versions=True,
    )
    alpha_record = store.fetch_record(
        "cloud-beta-only",
        project_ids=[alpha_id],
        include_versions=True,
    )

    assert beta_record is not None
    assert beta_record["project_id"] == beta_id
    assert alpha_record is None
    assert expectation["target_project"] == "beta"


@pytest.mark.integration
def test_pull_records_skips_unresolved_project_scope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    inline_cloud_calls: None,
    stable_git_roots: None,
) -> None:
    """Pull should skip remote records whose project name is unknown locally."""
    alpha_root = tmp_path / "projects" / "alpha"
    alpha_root.mkdir(parents=True)
    config = _make_cloud_config(tmp_path, projects={"alpha": alpha_root})
    state = _ShipperState()

    monkeypatch.setattr(
        "lerim.cloud.shipper._get_json_sync",
        lambda *args, **kwargs: {
            "records": [
                {
                    "record_id": "cloud-missing-project",
                    "record_kind": "fact",
                    "title": "Should be skipped",
                    "body": "Unknown project names must not fall back.",
                    "status": "active",
                    "project": "beta",
                    "cloud_edited_at": "2026-04-20T12:30:00Z",
                }
            ]
        },
    )

    pulled = asyncio.run(_pull_records("https://api.test", "tok-test", config, state))

    assert pulled == 0
    store = ContextStore(config.context_db_path)
    alpha_id = _project_id_for(alpha_root)
    assert store.fetch_record("cloud-missing-project", project_ids=[alpha_id]) is None


@pytest.mark.integration
def test_ship_and_pull_round_trip_core_fields(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    inline_cloud_calls: None,
    stable_git_roots: None,
) -> None:
    """Ship and pull should preserve one record's core sync fields across DBs."""
    expectation = load_cloud_expectation("ship_and_pull_round_trip_core_fields")["expected"]
    source_root = tmp_path / "source"
    source_root.mkdir()
    project_root = tmp_path / "projects" / "alpha"
    project_root.mkdir(parents=True)
    source_config = _make_cloud_config(source_root, projects={"alpha": project_root})
    source_state = _ShipperState()

    source_store = ContextStore(source_config.context_db_path)
    source_store.initialize()
    source_project_id = _project_id_for(project_root)
    source_store.register_project(resolve_project_identity(project_root))
    source_store.create_record(
        project_id=source_project_id,
        session_id=None,
        record_id="roundtrip-core",
        kind="fact",
        title="Cloud sync contract",
        body="Round trips should keep the durable body intact.",
        status="archived",
        created_at="2026-03-01T00:00:00Z",
        updated_at="2026-04-01T00:00:00Z",
        valid_from="2026-03-01T00:00:00Z",
        valid_until="2026-04-10T00:00:00Z",
    )

    captured_payloads: list[dict[str, Any]] = []

    async def _capture_post(endpoint: str, path: str, token: str, payload: dict[str, Any]) -> bool:
        captured_payloads.append(payload)
        return True

    monkeypatch.setattr("lerim.cloud.shipper._post_batch", _capture_post)

    shipped = asyncio.run(
        _ship_records("https://api.test", "tok-test", source_config, source_state)
    )

    assert shipped == 1
    assert source_state.records_shipped_at
    assert captured_payloads

    shipped_record = captured_payloads[0]["records"][0]
    cloud_record = {
        **shipped_record,
        "cloud_edited_at": str(shipped_record["updated"]),
    }

    target_root = tmp_path / "target"
    target_root.mkdir()
    target_config = _make_cloud_config(target_root, projects={"alpha": project_root})
    target_state = _ShipperState()

    monkeypatch.setattr(
        "lerim.cloud.shipper._get_json_sync",
        lambda *args, **kwargs: {"records": [cloud_record]},
    )

    pulled = asyncio.run(_pull_records("https://api.test", "tok-test", target_config, target_state))

    assert pulled == 1
    assert target_state.records_pulled_at == cloud_record["cloud_edited_at"]

    target_store = ContextStore(target_config.context_db_path)
    target_record = target_store.fetch_record(
        "roundtrip-core",
        project_ids=[source_project_id],
        include_versions=True,
    )
    assert target_record is not None
    assert target_record["project_id"] == source_project_id
    assert target_record["kind"] == expectation["kind"]
    assert target_record["title"] == "Cloud sync contract"
    assert target_record["body"] == "Round trips should keep the durable body intact."
    assert target_record["status"] == expectation["status"]
    assert target_record["created_at"] == "2026-03-01T00:00:00Z"
    assert target_record["updated_at"] == "2026-04-01T00:00:00Z"
    assert target_record["valid_from"] == "2026-03-01T00:00:00Z"
    assert target_record["valid_until"] == "2026-04-10T00:00:00Z"
    assert len(target_record["versions"]) == 1
