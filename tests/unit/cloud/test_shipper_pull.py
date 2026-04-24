"""Tests for cloud shipper pull functions."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from unittest.mock import MagicMock, patch

import pytest

from lerim.cloud.shipper import (
    _ShipperState,
    _normalize_cloud_kind,
    _pull_records,
    _typed_fields_from_cloud_record,
    _upsert_pulled_record,
)
from lerim.context import ContextStore, resolve_project_identity
from tests.helpers import make_config


@pytest.fixture
def mock_embeddings(monkeypatch):
    provider = MagicMock()
    provider.embedding_dims = 384
    provider.model_id = "test-model"
    provider.embed_document.return_value = [0.1] * 384
    provider.embed_query.return_value = [0.1] * 384
    monkeypatch.setattr("lerim.context.store.get_embedding_provider", lambda: provider)
    monkeypatch.setattr(
        "lerim.context.embedding.get_embedding_provider", lambda: provider
    )


class TestNormalizeCloudKind:
    """Tests for _normalize_cloud_kind."""

    def test_canonical_kinds_pass_through(self):
        for kind in (
            "decision",
            "preference",
            "constraint",
            "fact",
            "reference",
            "episode",
        ):
            assert _normalize_cloud_kind(kind) == kind

    @pytest.mark.parametrize(
        "kind",
        ["project", "learning", "feedback", "implementation", "custom_type", None],
    )
    def test_invalid_kinds_raise(self, kind):
        with pytest.raises(ValueError, match="invalid_cloud_record_kind"):
            _normalize_cloud_kind(kind)

    def test_case_insensitive(self):
        assert _normalize_cloud_kind("Decision") == "decision"
        assert _normalize_cloud_kind("FACT") == "fact"

    def test_whitespace_stripped(self):
        assert _normalize_cloud_kind("  decision  ") == "decision"


class TestTypedFieldsFromCloudRecord:
    """Tests for _typed_fields_from_cloud_record."""

    def test_decision_kind(self):
        record = {"decision": "Use typed tools", "why": "No raw SQL"}
        result = _typed_fields_from_cloud_record(record, kind="decision")
        assert result["decision"] == "Use typed tools"
        assert result["why"] == "No raw SQL"

    def test_episode_kind(self):
        record = {
            "user_intent": "fix bug",
            "what_happened": "Fixed the importer bug",
        }
        result = _typed_fields_from_cloud_record(record, kind="episode")
        assert result["user_intent"] == "fix bug"
        assert result["what_happened"] == "Fixed the importer bug"

    def test_fact_kind_returns_empty(self):
        record = {"title": "X depends on Y", "body": "some body"}
        result = _typed_fields_from_cloud_record(record, kind="fact")
        assert result == {}

    def test_decision_does_not_use_name_or_description(self):
        record = {"name": "Named decision", "description": "desc"}
        result = _typed_fields_from_cloud_record(record, kind="decision")
        assert result["decision"] == ""
        assert result["why"] == ""

    def test_episode_does_not_use_description_fallback(self):
        record = {"description": "user intent here"}
        result = _typed_fields_from_cloud_record(record, kind="episode")
        assert result["user_intent"] == ""
        assert result["what_happened"] == ""


class TestUpsertPulledRecord:
    """Tests for _upsert_pulled_record."""

    def test_skips_empty_record_id(self, tmp_path):
        result = _upsert_pulled_record(
            context_db_path=tmp_path / "ctx.sqlite3",
            project_identity=resolve_project_identity(tmp_path),
            record={"record_id": ""},
        )
        assert result == "permanent_drop"

    def test_creates_new_record(self, tmp_path, monkeypatch, mock_embeddings):
        monkeypatch.setattr(
            "lerim.config.project_scope.git_root_for",
            lambda _p=None: tmp_path,
        )
        ctx_db = tmp_path / "context.sqlite3"
        result = _upsert_pulled_record(
            context_db_path=ctx_db,
            project_identity=resolve_project_identity(tmp_path),
            record={
                "record_id": "cloud-rec-1",
                "record_kind": "decision",
                "title": "Cloud Decision",
                "body": "Use typed tools",
                "decision": "Cloud Decision",
                "why": "Use typed tools",
                "status": "active",
                "cloud_edited_at": "2026-04-01T12:00:00Z",
            },
        )
        assert result == "applied"
        store = ContextStore(ctx_db)
        with store.connect() as conn:
            row = conn.execute(
                "SELECT title, kind FROM records WHERE record_id = ?",
                ("cloud-rec-1",),
            ).fetchone()
        assert row is not None
        assert row["title"] == "Cloud Decision"

    def test_updates_existing_record(self, tmp_path, monkeypatch, mock_embeddings):
        monkeypatch.setattr(
            "lerim.config.project_scope.git_root_for",
            lambda _p=None: tmp_path,
        )
        ctx_db = tmp_path / "context.sqlite3"
        _upsert_pulled_record(
            context_db_path=ctx_db,
            project_identity=resolve_project_identity(tmp_path),
            record={
                "record_id": "cloud-rec-2",
                "record_kind": "fact",
                "title": "Original Title",
                "body": "Original body",
                "status": "active",
                "cloud_edited_at": "2026-04-01T12:00:00Z",
            },
        )
        result = _upsert_pulled_record(
            context_db_path=ctx_db,
            project_identity=resolve_project_identity(tmp_path),
            record={
                "record_id": "cloud-rec-2",
                "record_kind": "fact",
                "title": "Updated Title",
                "body": "Updated body",
                "status": "active",
                "cloud_edited_at": "2026-04-02T12:00:00Z",
            },
        )
        assert result == "applied"
        store = ContextStore(ctx_db)
        with store.connect() as conn:
            row = conn.execute(
                "SELECT title FROM records WHERE record_id = ?",
                ("cloud-rec-2",),
            ).fetchone()
        assert row["title"] == "Updated Title"


class TestPullRecords:
    """Tests for _pull_records."""

    def test_returns_zero_not_configured(self, tmp_path):
        cfg = make_config(tmp_path)
        state = _ShipperState()
        result = asyncio.run(_pull_records("https://api.test", "tok", cfg, state))
        assert result == 0

    def test_no_data_returns_zero(self, tmp_path):
        proj_dir = tmp_path / "proj"
        proj_dir.mkdir()
        cfg = replace(make_config(tmp_path), projects={"proj": str(proj_dir)})
        state = _ShipperState()

        async def mock_to_thread(fn, *args, **kwargs):
            return None

        with patch("lerim.cloud.shipper.asyncio.to_thread", side_effect=mock_to_thread):
            pulled = asyncio.run(_pull_records("https://api.test", "tok", cfg, state))
        assert pulled == 0

    def test_empty_records_returns_zero(self, tmp_path):
        proj_dir = tmp_path / "proj"
        proj_dir.mkdir()
        cfg = replace(make_config(tmp_path), projects={"proj": str(proj_dir)})
        state = _ShipperState()

        async def mock_to_thread(fn, *args, **kwargs):
            return {"records": []}

        with patch("lerim.cloud.shipper.asyncio.to_thread", side_effect=mock_to_thread):
            pulled = asyncio.run(_pull_records("https://api.test", "tok", cfg, state))
        assert pulled == 0

    def test_invalid_kind_advances_watermark_as_permanent_drop(
        self, tmp_path, mock_embeddings
    ):
        """Invalid cloud kinds are permanent drops and can advance the watermark."""
        proj_dir = tmp_path / "proj"
        proj_dir.mkdir()
        cfg = replace(make_config(tmp_path), projects={"proj": str(proj_dir)})
        state = _ShipperState(records_pulled_at="2026-03-01T00:00:00Z")
        cloud_data = {
            "records": [
                {
                    "record_id": "bad-kind",
                    "record_kind": "project",
                    "title": "Unsupported kind",
                    "body": "This old cloud kind cannot be stored locally.",
                    "project": "proj",
                    "cloud_edited_at": "2026-04-01T12:00:00Z",
                }
            ]
        }

        async def mock_to_thread(fn, *args, **kwargs):
            return cloud_data

        with patch("lerim.cloud.shipper.asyncio.to_thread", side_effect=mock_to_thread):
            pulled = asyncio.run(_pull_records("https://api.test", "tok", cfg, state))

        assert pulled == 0
        assert state.records_pulled_at == "2026-04-01T12:00:00Z"
        store = ContextStore(cfg.context_db_path)
        with store.connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM records WHERE record_id = ?",
                ("bad-kind",),
            ).fetchone()
        assert row is None

    def test_upsert_failure_does_not_advance_watermark(
        self, tmp_path, mock_embeddings
    ):
        """Validation failures are retryable and must not move the watermark."""
        proj_dir = tmp_path / "proj"
        proj_dir.mkdir()
        cfg = replace(make_config(tmp_path), projects={"proj": str(proj_dir)})
        state = _ShipperState(records_pulled_at="2026-03-01T00:00:00Z")
        cloud_data = {
            "records": [
                {
                    "record_id": "missing-required-fields",
                    "record_kind": "decision",
                    "title": "Incomplete decision",
                    "body": "Decision typed fields are required locally.",
                    "project": "proj",
                    "cloud_edited_at": "2026-04-01T12:00:00Z",
                }
            ]
        }

        async def mock_to_thread(fn, *args, **kwargs):
            return cloud_data

        with patch("lerim.cloud.shipper.asyncio.to_thread", side_effect=mock_to_thread):
            pulled = asyncio.run(_pull_records("https://api.test", "tok", cfg, state))

        assert pulled == 0
        assert state.records_pulled_at == "2026-03-01T00:00:00Z"
        store = ContextStore(cfg.context_db_path)
        with store.connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM records WHERE record_id = ?",
                ("missing-required-fields",),
            ).fetchone()
        assert row is None

    def test_unknown_project_does_not_advance_watermark(self, tmp_path):
        """Unknown local projects are retryable and must not move the watermark."""
        proj_dir = tmp_path / "alpha"
        proj_dir.mkdir()
        cfg = replace(make_config(tmp_path), projects={"alpha": str(proj_dir)})
        state = _ShipperState(records_pulled_at="2026-03-01T00:00:00Z")
        cloud_data = {
            "records": [
                {
                    "record_id": "cloud-unknown-project",
                    "record_kind": "fact",
                    "title": "Should wait",
                    "body": "This can sync if the project is configured later.",
                    "project": "beta",
                    "cloud_edited_at": "2026-04-01T12:00:00Z",
                }
            ]
        }

        async def mock_to_thread(fn, *args, **kwargs):
            return cloud_data

        with patch("lerim.cloud.shipper.asyncio.to_thread", side_effect=mock_to_thread):
            pulled = asyncio.run(_pull_records("https://api.test", "tok", cfg, state))

        assert pulled == 0
        assert state.records_pulled_at == "2026-03-01T00:00:00Z"
