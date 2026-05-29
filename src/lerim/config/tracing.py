"""MLflow tracing for Lerim agent observability.

Activates MLflow when ``LERIM_MLFLOW=true`` is set. Agent pipelines emit
explicit spans through Lerim's runtime instrumentation. When
``MLFLOW_TRACKING_URI`` is configured, traces go to the shared tracking server;
otherwise Lerim keeps the legacy local SQLite fallback.
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


def _is_sqlite_tracking_uri(tracking_uri: str) -> bool:
	"""Return True when MLflow uses a local SQLite backend."""
	return tracking_uri.strip().lower().startswith("sqlite:///")


def _configured_string(config: Config, name: str, default: str = "") -> str:
	"""Read a string config attribute defensively for old test doubles."""
	value = getattr(config, name, default)
	return value.strip() if isinstance(value, str) else default


def _configured_bool(config: Config, name: str, default: bool = False) -> bool:
	"""Read a bool config attribute defensively for old test doubles."""
	value = getattr(config, name, default)
	return value if isinstance(value, bool) else default


def _raise_if_required(config: Config, message: str, exc: Exception | None = None) -> None:
	"""Raise a clear MLflow startup error when strict tracing is enabled."""
	if not _configured_bool(config, "mlflow_required", False):
		return
	error = RuntimeError(
		f"{message} Start shared MLflow with: "
		"cd ~/codes/personal/local-mlflow && docker compose up -d"
	)
	if exc is not None:
		raise error from exc
	raise error


def _default_sqlite_tracking(config: Config) -> tuple[str, Path]:
	"""Return Lerim's legacy SQLite tracking URI and DB path."""
	db_path = Path(config.global_data_dir).expanduser() / "observability" / "mlflow.db"
	return f"sqlite:///{db_path}", db_path


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

	# Disable external OTel OTLP export. MLflow owns trace export here, and a
	# stale OTEL endpoint causes noisy "Exception while exporting Span" errors.
	os.environ.pop("OTEL_EXPORTER_OTLP_ENDPOINT", None)
	os.environ.pop("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", None)

	configured_tracking_uri = _configured_string(config, "mlflow_tracking_uri")
	configured_experiment = _configured_string(config, "mlflow_experiment", experiment_name)
	if _configured_bool(config, "mlflow_required", False) and not configured_tracking_uri:
		_raise_if_required(
			config,
			"MLflow is required but MLFLOW_TRACKING_URI is not configured.",
		)
		return

	if configured_tracking_uri:
		tracking_uri = configured_tracking_uri
		db_path = None
	else:
		tracking_uri, db_path = _default_sqlite_tracking(config)
		db_path.parent.mkdir(parents=True, exist_ok=True)

	active_experiment = configured_experiment or experiment_name

	def _activate_mlflow() -> None:
		mlflow.set_tracking_uri(tracking_uri)
		mlflow.set_experiment(active_experiment)

	try:
		if _is_sqlite_tracking_uri(tracking_uri):
			_ensure_mlflow_schema(tracking_uri, str(db_path))
		_activate_mlflow()
		logger.info(
			"MLflow tracing enabled → {} experiment={}",
			tracking_uri,
			active_experiment,
		)
	except Exception as exc:
		if _is_recoverable_schema_reset_error(str(exc)):
			if db_path is None:
				_raise_if_required(
					config,
					f"MLflow is required but unavailable at {tracking_uri}.",
					exc,
				)
				logger.warning(
					"MLflow tracing disabled due to initialization error: {}",
					exc,
				)
				return
			backup_path = _backup_and_reset_mlflow_db(Path(db_path))
			logger.warning(
				"MLflow revision state is broken; backed up DB to {} and reinitializing {}",
				str(backup_path) if backup_path else "<none>",
				db_path,
			)
			try:
				if _is_sqlite_tracking_uri(tracking_uri):
					_ensure_mlflow_schema(tracking_uri, str(db_path))
				_activate_mlflow()
				logger.info(
					"MLflow tracing enabled → {} experiment={}",
					tracking_uri,
					active_experiment,
				)
				return
			except Exception as retry_exc:
				_raise_if_required(
					config,
					f"MLflow is required but unavailable at {tracking_uri}.",
					retry_exc,
				)
				logger.warning(
					"MLflow tracing disabled due to initialization error: {}",
					retry_exc,
				)
				return
		_raise_if_required(
			config,
			f"MLflow is required but unavailable at {tracking_uri}.",
			exc,
		)
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
