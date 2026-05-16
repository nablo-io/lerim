"""MLflow tracing for Lerim agent observability.

Activates MLflow when ``LERIM_MLFLOW=true`` is set. BAML/LangGraph flows emit
explicit spans through Lerim's runtime instrumentation.

Traces are stored under ``~/.lerim/observability/`` so observability files do
not clutter the root of the Lerim home directory.
"""

from __future__ import annotations

import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

import mlflow
from mlflow.exceptions import MlflowException
from mlflow.store.db.utils import (
	_initialize_tables,
	_upgrade_db,
	_verify_schema,
	create_sqlalchemy_engine,
)
from loguru import logger

from lerim.config.settings import Config


def _is_recoverable_schema_reset_error(message: str) -> bool:
	"""Return True when local MLflow DB should be reset and reinitialized."""
	text = message.lower()
	return (
		"can't locate revision" in text
		or "cannot locate revision" in text
		or "no such table" in text
		or "no such column" in text
	)


def _backup_and_reset_mlflow_db(db_path: Path) -> Path | None:
	"""Backup current MLflow DB and remove broken DB files for re-init."""
	if not db_path.exists():
		return None
	timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
	backup_dir = db_path.parent / "backups"
	backup_dir.mkdir(parents=True, exist_ok=True)
	backup_path = backup_dir / f"{db_path.stem}.backup-{timestamp}{db_path.suffix}"
	backup_path.parent.mkdir(parents=True, exist_ok=True)
	shutil.copy2(db_path, backup_path)
	for suffix in ("", "-wal", "-shm"):
		candidate = Path(str(db_path) + suffix)
		try:
			candidate.unlink()
		except FileNotFoundError:
			continue
	return backup_path


def _ensure_mlflow_schema(tracking_uri: str, db_path: str) -> None:
	"""Ensure MLflow SQLite schema matches installed MLflow revision.

	When MLflow is upgraded, existing local SQLite files may be on an older
	alembic revision. In that case we run the same DB upgrade used by
	``mlflow db upgrade`` so runtime startup remains seamless.
	"""
	engine = create_sqlalchemy_engine(tracking_uri)
	try:
		_verify_schema(engine)
	except MlflowException as exc:
		error_msg = str(exc).lower()
		if "out-of-date database schema" in error_msg:
			logger.info("Upgrading MLflow database schema at {}", db_path)
			try:
				_upgrade_db(engine)
			except Exception as upgrade_exc:
				if _is_recoverable_schema_reset_error(str(upgrade_exc)):
					backup_path = _backup_and_reset_mlflow_db(Path(db_path))
					logger.warning(
						"MLflow schema upgrade failed; backed up DB to {} and reinitializing {}",
						str(backup_path) if backup_path else "<none>",
						db_path,
					)
					fresh_engine = create_sqlalchemy_engine(tracking_uri)
					_initialize_tables(fresh_engine)
					return
				raise
			return
		if _is_recoverable_schema_reset_error(str(exc)):
			backup_path = _backup_and_reset_mlflow_db(Path(db_path))
			logger.warning(
				"MLflow revision state is broken; backed up DB to {} and reinitializing {}",
				str(backup_path) if backup_path else "<none>",
				db_path,
			)
			fresh_engine = create_sqlalchemy_engine(tracking_uri)
			_initialize_tables(fresh_engine)
			return
		raise
	except Exception as exc:
		if _is_recoverable_schema_reset_error(str(exc)):
			backup_path = _backup_and_reset_mlflow_db(Path(db_path))
			logger.warning(
				"MLflow revision state is broken; backed up DB to {} and reinitializing {}",
				str(backup_path) if backup_path else "<none>",
				db_path,
			)
			fresh_engine = create_sqlalchemy_engine(tracking_uri)
			_initialize_tables(fresh_engine)
			return
		raise


def configure_tracing(config: Config, experiment_name: str = "lerim") -> None:
	"""Activate MLflow tracing if enabled via env/config.

	Must be called once at startup before any agent is constructed.

	Args:
		config: Lerim config (must have mlflow_enabled=True to take effect).
		experiment_name: MLflow experiment to log into. Use "lerim" for production
			runs and "lerim-evals" (or any custom name) for evaluation runs so they
			show up in separate tabs in the MLflow UI.
	"""
	if not config.mlflow_enabled:
		return

	# Disable external OTel OTLP export — we use MLflow's SQLite backend only.
	# Without this, a stale OTEL_EXPORTER_OTLP_ENDPOINT env var causes
	# "Exception while exporting Span" errors on every LLM call.
	os.environ.pop("OTEL_EXPORTER_OTLP_ENDPOINT", None)
	os.environ.pop("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", None)

	db_path = Path(config.global_data_dir).expanduser() / "observability" / "mlflow.db"
	db_path.parent.mkdir(parents=True, exist_ok=True)
	tracking_uri = f"sqlite:///{db_path}"

	def _activate_mlflow() -> None:
		mlflow.set_tracking_uri(tracking_uri)
		mlflow.set_experiment(experiment_name)

	try:
		_ensure_mlflow_schema(tracking_uri, str(db_path))
		_activate_mlflow()
		logger.info(
			"MLflow tracing enabled → sqlite:///{} experiment={}",
			db_path,
			experiment_name,
		)
	except Exception as exc:
		if _is_recoverable_schema_reset_error(str(exc)):
			backup_path = _backup_and_reset_mlflow_db(Path(db_path))
			logger.warning(
				"MLflow revision state is broken; backed up DB to {} and reinitializing {}",
				str(backup_path) if backup_path else "<none>",
				db_path,
			)
			try:
				_ensure_mlflow_schema(tracking_uri, str(db_path))
				_activate_mlflow()
				logger.info(
					"MLflow tracing enabled → sqlite:///{} experiment={}",
					db_path,
					experiment_name,
				)
				return
			except Exception as retry_exc:
				logger.warning(
					"MLflow tracing disabled due to initialization error: {}",
					retry_exc,
				)
				return
		logger.warning(
			"MLflow tracing disabled due to initialization error: {}",
			exc,
		)


if __name__ == "__main__":
	"""Minimal self-test: configure_tracing runs without error."""
	from lerim.config.settings import load_config

	cfg = load_config()
	configure_tracing(cfg)
	state = "enabled" if cfg.mlflow_enabled else "disabled"
	print(f"tracing.py self-test passed (mlflow {state})")
