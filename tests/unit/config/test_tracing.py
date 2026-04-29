"""Tests for MLflow tracing configuration and schema upgrade handling."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

from mlflow.exceptions import MlflowException

_mock_mlflow = MagicMock()
_mock_mlflow_pydantic_ai = MagicMock()
_mock_mlflow.pydantic_ai = _mock_mlflow_pydantic_ai

if "mlflow" not in sys.modules:
	sys.modules["mlflow"] = _mock_mlflow
	sys.modules["mlflow.pydantic_ai"] = _mock_mlflow_pydantic_ai

from lerim.config.tracing import configure_tracing  # noqa: E402


def _make_config(*, enabled: bool):
	cfg = MagicMock()
	cfg.mlflow_enabled = enabled
	cfg.global_data_dir = MagicMock()
	cfg.global_data_dir.__truediv__.return_value = "/tmp/mlflow.db"
	return cfg


@patch("lerim.config.tracing.mlflow")
def test_tracing_disabled_does_nothing(mock_mlflow: MagicMock) -> None:
	configure_tracing(_make_config(enabled=False))
	mock_mlflow.set_experiment.assert_not_called()
	mock_mlflow.pydantic_ai.autolog.assert_not_called()


@patch("lerim.config.tracing.mlflow")
@patch("lerim.config.tracing._ensure_mlflow_schema")
def test_tracing_enabled_sets_experiment_and_autologs(
	mock_ensure_schema: MagicMock, mock_mlflow: MagicMock
) -> None:
	configure_tracing(_make_config(enabled=True))
	mock_ensure_schema.assert_called_once()
	mock_mlflow.set_experiment.assert_called_once_with("lerim")
	mock_mlflow.pydantic_ai.autolog.assert_called_once()


@patch("lerim.config.tracing.mlflow")
@patch("lerim.config.tracing._ensure_mlflow_schema")
def test_tracing_init_error_disables_tracing_without_crashing(
	mock_ensure_schema: MagicMock, mock_mlflow: MagicMock
) -> None:
	mock_ensure_schema.side_effect = RuntimeError("schema mismatch")
	configure_tracing(_make_config(enabled=True))
	mock_mlflow.set_experiment.assert_not_called()
	mock_mlflow.pydantic_ai.autolog.assert_not_called()


@patch("lerim.config.tracing.mlflow")
@patch("lerim.config.tracing._backup_and_reset_mlflow_db")
@patch("lerim.config.tracing._ensure_mlflow_schema")
def test_tracing_recovers_missing_revision_during_activation(
	mock_ensure_schema: MagicMock,
	mock_backup: MagicMock,
	mock_mlflow: MagicMock,
) -> None:
	mock_backup.return_value = "/tmp/mlflow.backup.db"
	mock_mlflow.set_experiment.side_effect = [
		RuntimeError("Can't locate revision identified by 'deadbeef'"),
		None,
	]

	configure_tracing(_make_config(enabled=True))

	assert mock_ensure_schema.call_count == 2
	mock_backup.assert_called_once()
	assert mock_mlflow.set_experiment.call_count == 2
	mock_mlflow.pydantic_ai.autolog.assert_called_once()


@patch("lerim.config.tracing._upgrade_db")
@patch("lerim.config.tracing._verify_schema")
@patch("lerim.config.tracing.create_sqlalchemy_engine")
def test_ensure_schema_upgrades_outdated_db(
	mock_create_engine: MagicMock,
	mock_verify_schema: MagicMock,
	mock_upgrade_db: MagicMock,
) -> None:
	engine = MagicMock()
	mock_create_engine.return_value = engine
	mock_verify_schema.side_effect = MlflowException(
		"Detected out-of-date database schema"
	)

	from lerim.config.tracing import _ensure_mlflow_schema

	_ensure_mlflow_schema("sqlite:////tmp/mlflow.db", "/tmp/mlflow.db")
	mock_upgrade_db.assert_called_once_with(engine)


@patch("lerim.config.tracing._upgrade_db")
@patch("lerim.config.tracing._verify_schema")
@patch("lerim.config.tracing.create_sqlalchemy_engine")
def test_ensure_schema_raises_non_schema_errors(
	mock_create_engine: MagicMock,
	mock_verify_schema: MagicMock,
	mock_upgrade_db: MagicMock,
) -> None:
	engine = MagicMock()
	mock_create_engine.return_value = engine
	mock_verify_schema.side_effect = MlflowException("permission denied")

	from lerim.config.tracing import _ensure_mlflow_schema

	try:
		_ensure_mlflow_schema("sqlite:////tmp/mlflow.db", "/tmp/mlflow.db")
	except MlflowException:
		pass
	else:
		raise AssertionError("Expected MlflowException for non-schema errors")
	mock_upgrade_db.assert_not_called()


@patch("lerim.config.tracing._initialize_tables")
@patch("lerim.config.tracing._backup_and_reset_mlflow_db")
@patch("lerim.config.tracing._verify_schema")
@patch("lerim.config.tracing.create_sqlalchemy_engine")
def test_ensure_schema_recovers_missing_revision_by_backup_and_reinit(
	mock_create_engine: MagicMock,
	mock_verify_schema: MagicMock,
	mock_backup: MagicMock,
	mock_initialize_tables: MagicMock,
) -> None:
	engine = MagicMock()
	mock_create_engine.return_value = engine
	mock_verify_schema.side_effect = MlflowException(
		"Can't locate revision identified by 'deadbeef'"
	)
	mock_backup.return_value = "/tmp/mlflow.backup.db"

	from lerim.config.tracing import _ensure_mlflow_schema

	_ensure_mlflow_schema("sqlite:////tmp/mlflow.db", "/tmp/mlflow.db")
	mock_backup.assert_called_once()
	mock_initialize_tables.assert_called_once()
