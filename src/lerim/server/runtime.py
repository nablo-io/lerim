"""Runtime orchestrator for Lerim sync, maintain, and ask (PydanticAI only).

All three flows run through PydanticAI models and shared retry/fallback logic.
"""

from __future__ import annotations

import json
import logging
import re
import secrets
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from lerim.agents.ask import format_ask_hints, run_ask
from lerim.agents.contracts import MaintainResultContract, SyncResultContract
from lerim.agents.extract import ExtractionResult, run_extraction
from lerim.agents.maintain import run_maintain
from lerim.config.providers import build_pydantic_model
from lerim.config.settings import Config, get_config
from lerim.context import ContextStore, resolve_project_identity
from pydantic_ai.messages import ModelMessage, ModelMessagesTypeAdapter

logger = logging.getLogger("lerim.runtime")
_LAST_N_PATTERN = re.compile(r"\b(?:last|latest)\s+(\d+)\s+learnings?\b", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def _default_run_folder_name(prefix: str = "sync") -> str:
	"""Build deterministic per-run workspace folder name with given prefix."""
	stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
	return f"{prefix}-{stamp}-{secrets.token_hex(3)}"


def build_maintain_artifact_paths(run_folder: Path) -> dict[str, Path]:
	"""Return canonical workspace artifact paths for a maintain run folder."""
	return {
		"agent_log": run_folder / "agent.log",
		"subagents_log": run_folder / "subagents.log",
	}


def _build_artifact_paths(run_folder: Path) -> dict[str, Path]:
	"""Return canonical workspace artifact paths for a sync run folder."""
	return {
		"agent_log": run_folder / "agent.log",
		"subagents_log": run_folder / "subagents.log",
		"session_log": run_folder / "session.log",
	}


def _resolve_runtime_roots(
	*,
	config: Config,
) -> Path:
	"""Return the canonical global workspace root.

	Run artifacts are always written under ``~/.lerim/workspace``.
	The DB-only architecture no longer allows callers to redirect
	artifacts into repo-local ``.lerim`` trees or any other custom path.
	"""
	return config.global_data_dir / "workspace"


def _store_for_config(config: Config) -> ContextStore:
	"""Return the canonical context store for the current config."""
	store = ContextStore(config.context_db_path)
	store.initialize()
	return store


def _record_change_counts(config: Config, session_id: str) -> dict[str, int]:
	"""Count record version mutations written by one session-scoped agent run."""
	store = _store_for_config(config)
	with store.connect() as conn:
		rows = conn.execute(
			"""
			SELECT change_kind, COUNT(1) AS total
			FROM record_versions
			WHERE changed_by_session_id = ?
			GROUP BY change_kind
			""",
			(session_id,),
		).fetchall()
	return {str(row["change_kind"]): int(row["total"]) for row in rows}


def _is_learning_row(row: dict[str, Any]) -> bool:
	"""Return whether one record row should count as a learning."""
	return str(row.get("kind") or "") != "episode"


def _format_direct_rows(rows: list[dict[str, Any]]) -> str:
	"""Format a short deterministic list of record rows."""
	if not rows:
		return "No matching records found."
	lines = []
	for idx, row in enumerate(rows, start=1):
		title = str(row.get("title") or "").strip() or "(untitled)"
		kind = str(row.get("kind") or "?")
		created_at = str(row.get("created_at") or "")
		lines.append(f"{idx}. [{kind}] {title} ({created_at})")
	return "\n".join(lines)


def _format_episode_rows(rows: list[dict[str, Any]]) -> str:
	"""Format episode rows as short session recaps."""
	if not rows:
		return "No matching episodes found."
	lines = []
	for idx, row in enumerate(rows, start=1):
		title = str(row.get("title") or "").strip() or "(untitled)"
		happened = str(row.get("what_happened") or row.get("body") or "").strip()
		preview = happened[:240]
		created_at = str(row.get("created_at") or "")
		lines.append(f"{idx}. {title} ({created_at})\n   {preview}")
	return "\n".join(lines)


def _direct_ask_answer(
	*,
	store: ContextStore,
	project_ids: list[str],
	question: str,
) -> str | None:
	"""Answer simple analytic ask questions deterministically before using the model."""
	text = question.strip()
	lowered = text.lower()

	if "how many" in lowered and any(token in lowered for token in ("record", "records", "memory", "memories")):
		payload = store.query(entity="records", mode="count", project_ids=project_ids)
		return f"There are {int(payload['count'])} records extracted."

	if "how many" in lowered and "learning" in lowered:
		payload = store.query(entity="records", mode="list", project_ids=project_ids, order_by="created_at", limit=500)
		rows = [row for row in payload["rows"] if _is_learning_row(row)]
		return f"There are {len(rows)} learnings extracted."

	if any(phrase in lowered for phrase in ("what is the last memory", "what's the last memory", "latest memory", "last record", "latest record")):
		payload = store.query(entity="records", mode="list", project_ids=project_ids, order_by="created_at", limit=50)
		rows = [row for row in payload["rows"] if _is_learning_row(row)]
		if not rows:
			return "No records found."
		row = rows[0]
		title = str(row.get("title") or "").strip() or "(untitled)"
		return (
			f"The latest record is [{row['kind']}] {title} "
			f"created at {row['created_at']}."
		)

	match = _LAST_N_PATTERN.search(text)
	if match:
		limit = max(1, min(int(match.group(1)), 50))
		payload = store.query(entity="records", mode="list", project_ids=project_ids, order_by="created_at", limit=200)
		rows = [row for row in payload["rows"] if _is_learning_row(row)][:limit]
		if not rows:
			return "No learnings found."
		return _format_direct_rows(rows)

	if "yesterday" in lowered and "learning" in lowered:
		now = datetime.now(timezone.utc)
		start = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
		end = now.replace(hour=0, minute=0, second=0, microsecond=0)
		payload = store.query(
			entity="records",
			mode="list",
			project_ids=project_ids,
			order_by="created_at",
			created_since=start.isoformat(),
			created_until=end.isoformat(),
			limit=200,
		)
		rows = [row for row in payload["rows"] if _is_learning_row(row)]
		if not rows:
			return "No learnings were created yesterday."
		return _format_direct_rows(rows)

	if "what happened yesterday" in lowered or ("yesterday" in lowered and "happened" in lowered):
		now = datetime.now(timezone.utc)
		start = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
		end = now.replace(hour=0, minute=0, second=0, microsecond=0)
		payload = store.query(
			entity="records",
			mode="list",
			project_ids=project_ids,
			order_by="created_at",
			kind="episode",
			created_since=start.isoformat(),
			created_until=end.isoformat(),
			limit=20,
		)
		rows = payload["rows"]
		if not rows:
			return "No episodes were created yesterday."
		return _format_episode_rows(rows)

	if "main learnings" in lowered:
		payload = store.query(entity="records", mode="list", project_ids=project_ids, order_by="updated_at", limit=20)
		rows = [row for row in payload["rows"] if _is_learning_row(row)][:5]
		if not rows:
			return "No learnings found."
		return _format_direct_rows(rows)

	return None


# ---------------------------------------------------------------------------
# Artifact I/O
# ---------------------------------------------------------------------------


def _write_json_artifact(path: Path, payload: dict[str, Any]) -> None:
	"""Write artifact payload as UTF-8 JSON with trailing newline."""
	path.write_text(
		json.dumps(payload, ensure_ascii=True, indent=2) + "\n", encoding="utf-8"
	)


def _write_text_with_newline(path: Path, content: str) -> None:
	"""Write text artifact ensuring exactly one trailing newline."""
	text = content if content.endswith("\n") else f"{content}\n"
	path.write_text(text, encoding="utf-8")


def _write_agent_trace(path: Path, messages: list[ModelMessage]) -> None:
	"""Serialize PydanticAI message history to a stable JSON artifact."""
	trace_data = ModelMessagesTypeAdapter.dump_python(messages, mode="json")
	path.write_text(json.dumps(trace_data, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Quota error detection (PydanticAI path)
# ---------------------------------------------------------------------------


def _is_quota_error_pydantic(exc: Exception) -> bool:
	"""Detect rate-limit / quota errors across PydanticAI provider backends."""
	try:
		from openai import APIStatusError, RateLimitError
	except ImportError:
		RateLimitError = APIStatusError = None
	try:
		from httpx import HTTPStatusError
	except ImportError:
		HTTPStatusError = None

	if RateLimitError is not None and isinstance(exc, RateLimitError):
		return True
	if (
		APIStatusError is not None
		and isinstance(exc, APIStatusError)
		and getattr(exc, "status_code", None) == 429
	):
		return True
	if HTTPStatusError is not None and isinstance(exc, HTTPStatusError):
		try:
			if exc.response.status_code == 429:
				return True
		except Exception:
			pass

	msg = str(exc).lower()
	return "429" in msg or "rate limit" in msg or "quota" in msg


class LerimRuntime:
	"""Runtime orchestrator — PydanticAI sync, maintain, and ask."""

	def __init__(
		self,
		default_cwd: str | None = None,
		config: Config | None = None,
	) -> None:
		"""Create runtime with validated provider configuration."""
		cfg = config or get_config()
		self.config = cfg
		self._default_cwd = default_cwd

		from lerim.config.providers import validate_provider_for_role

		validate_provider_for_role(cfg.agent_role.provider, "agent")

	@staticmethod
	def generate_session_id() -> str:
		"""Generate a unique session ID for ask mode."""
		return f"lerim-{secrets.token_hex(6)}"

	def _run_with_fallback(
		self,
		*,
		flow: str,
		callable_fn: Callable[[Any], Any],
		model_builders: list[Callable[[], Any]],
		max_attempts: int = 3,
	) -> Any:
		"""Run a PydanticAI callable with retry + model-builder fallback support."""
		from pydantic_ai.exceptions import UsageLimitExceeded

		last_exc: Exception | None = None
		for model_idx, builder in enumerate(model_builders):
			model_label = (
				self.config.agent_role.model
				if model_idx == 0
				else f"fallback-{model_idx}"
			)
			for attempt in range(1, max_attempts + 1):
				try:
					logger.info(
						f"[{flow}] pydantic-ai attempt {attempt}/{max_attempts} "
						f"(model={model_label})"
					)
					model = builder()
					return callable_fn(model)
				except UsageLimitExceeded as exc:
					logger.warning(f"[{flow}] usage limit exceeded, short-circuiting: {exc}")
					raise
				except Exception as exc:
					last_exc = exc
					if isinstance(exc, ValueError):
						logger.error(f"[{flow}] non-retryable agent/store error: {str(exc)[:100]}")
						raise
					if _is_quota_error_pydantic(exc):
						logger.warning(f"[{flow}] quota error on {model_label}: {str(exc)[:100]}")
						break
					if attempt < max_attempts:
						wait_time = min(2 ** attempt, 8)
						logger.warning(
							f"[{flow}] transient error on attempt {attempt}/{max_attempts} "
							f"({type(exc).__name__}): {str(exc)[:100]}; retrying in {wait_time}s..."
						)
						time.sleep(wait_time)
						continue
					logger.error(f"[{flow}] exhausted retries on {model_label}: {str(exc)[:100]}")
					break

		raise RuntimeError(
			f"[{flow}] Failed after trying {len(model_builders)} model(s). "
			f"Last error: {last_exc}"
		) from last_exc

	# ------------------------------------------------------------------
	# Sync flow
	# ------------------------------------------------------------------

	def sync(
		self,
		trace_path: str | Path,
		session_id: str | None = None,
		agent_type: str = "unknown",
		session_meta: dict[str, Any] | None = None,
		adapter: Any | None = None,
	) -> dict[str, Any]:
		"""Run record-write sync flow and return stable contract payload."""
		del adapter  # retained for older call-sites; no longer used
		trace_file = Path(trace_path).expanduser().resolve()
		if not trace_file.exists() or not trace_file.is_file():
			raise FileNotFoundError(f"trace_path_missing:{trace_file}")

		repo_root = Path(self._default_cwd or Path.cwd()).expanduser().resolve()
		return self._sync_inner(
			trace_file,
			repo_root,
			session_id=session_id,
			agent_type=agent_type,
			session_meta=session_meta or {},
		)

	def _sync_inner(
		self,
		trace_file: Path,
		repo_root: Path,
		*,
		session_id: str | None,
		agent_type: str,
		session_meta: dict[str, Any],
	) -> dict[str, Any]:
		"""Inner sync logic called by sync()."""
		project_identity = resolve_project_identity(repo_root)
		resolved_workspace_root = _resolve_runtime_roots(
			config=self.config,
		)
		store = _store_for_config(self.config)
		store.register_project(project_identity)
		resolved_session_id = session_id or trace_file.stem or self.generate_session_id()
		store.upsert_session(
			project_id=project_identity.project_id,
			session_id=resolved_session_id,
			agent_type=agent_type,
			source_trace_ref=str(trace_file),
			repo_path=str(project_identity.repo_path),
			cwd=str(session_meta.get("cwd") or project_identity.repo_path),
			started_at=str(session_meta.get("started_at") or ""),
			model_name=str(self.config.agent_role.model),
			instructions_text=str(session_meta.get("instructions_text") or "")[:4000] or None,
			prompt_text=str(session_meta.get("prompt_text") or "")[:4000] or None,
			metadata=session_meta,
		)
		run_folder = resolved_workspace_root / _default_run_folder_name("sync")
		run_folder = resolved_workspace_root / "sync" / run_folder.name
		run_folder.mkdir(parents=True, exist_ok=True)
		artifact_paths = _build_artifact_paths(run_folder)

		metadata = {
			"run_id": run_folder.name,
			"trace_path": str(trace_file),
			"repo_name": repo_root.name,
		}
		_write_json_artifact(artifact_paths["session_log"], metadata)
		artifact_paths["subagents_log"].write_text("", encoding="utf-8")

		def _primary_builder() -> Any:
			return build_pydantic_model("agent", config=self.config)

		def _call(model: Any) -> tuple[ExtractionResult, list[ModelMessage]]:
			return run_extraction(
				context_db_path=self.config.context_db_path,
				project_identity=project_identity,
				session_id=resolved_session_id,
				trace_path=trace_file,
				model=model,
				run_folder=run_folder,
				return_messages=True,
			)

		result, messages = self._run_with_fallback(
			flow="sync",
			callable_fn=_call,
			model_builders=[_primary_builder],
		)

		response_text = (result.completion_summary or "").strip() or "(no response)"
		_write_text_with_newline(artifact_paths["agent_log"], response_text)

		agent_trace_path = run_folder / "agent_trace.json"
		try:
			_write_agent_trace(agent_trace_path, messages)
		except Exception as exc:
			logger.warning(f"[sync] Failed to write agent trace: {exc}")
			agent_trace_path.write_text("[]", encoding="utf-8")

		counts = _record_change_counts(self.config, resolved_session_id)

		payload = {
			"trace_path": str(trace_file),
			"context_db_path": str(self.config.context_db_path),
			"project_id": project_identity.project_id,
			"workspace_root": str(resolved_workspace_root),
			"run_folder": str(run_folder),
			"artifacts": {key: str(path) for key, path in artifact_paths.items()},
			"records_created": int(counts.get("create") or 0),
			"records_updated": int(counts.get("update") or 0) + int(counts.get("supersede") or 0),
			"records_archived": int(counts.get("archive") or 0),
			"cost_usd": 0.0,
		}
		return SyncResultContract.model_validate(payload).model_dump(mode="json")

	# ------------------------------------------------------------------
	# Maintain flow
	# ------------------------------------------------------------------

	def maintain(
		self,
		repo_root: str | Path | None = None,
		session_id: str | None = None,
	) -> dict[str, Any]:
		"""Run context-store maintenance flow and return stable contract payload."""
		resolved_repo_root = Path(repo_root).expanduser().resolve() if repo_root else Path(self._default_cwd or Path.cwd()).expanduser().resolve()
		return self._maintain_inner(
			resolved_repo_root,
			session_id=session_id or self.generate_session_id(),
		)

	def _maintain_inner(
		self,
		repo_root: Path,
		*,
		session_id: str,
	) -> dict[str, Any]:
		"""Inner maintain logic called by maintain()."""
		project_identity = resolve_project_identity(repo_root)
		resolved_workspace_root = _resolve_runtime_roots(
			config=self.config,
		)
		store = _store_for_config(self.config)
		store.register_project(project_identity)
		store.upsert_session(
			project_id=project_identity.project_id,
			session_id=session_id,
			agent_type="maintain",
			source_trace_ref=f"maintain:{project_identity.project_id}",
			repo_path=str(project_identity.repo_path),
			cwd=str(project_identity.repo_path),
			started_at=datetime.now(timezone.utc).isoformat(),
			model_name=str(self.config.agent_role.model),
			instructions_text=None,
			prompt_text=None,
			metadata={},
		)
		run_folder = resolved_workspace_root / _default_run_folder_name("maintain")
		run_folder = resolved_workspace_root / "maintain" / run_folder.name
		run_folder.mkdir(parents=True, exist_ok=True)
		artifact_paths = build_maintain_artifact_paths(run_folder)

		def _primary_builder() -> Any:
			return build_pydantic_model("agent", config=self.config)

		def _call(model: Any) -> tuple[Any, list[ModelMessage]]:
			return run_maintain(
				context_db_path=self.config.context_db_path,
				project_identity=project_identity,
				session_id=session_id,
				model=model,
				request_limit=self.config.agent_role.max_iters_maintain,
				return_messages=True,
			)

		result, messages = self._run_with_fallback(
			flow="maintain",
			callable_fn=_call,
			model_builders=[_primary_builder],
		)

		response_text = (result.completion_summary or "").strip() or "(no response)"
		_write_text_with_newline(artifact_paths["agent_log"], response_text)

		agent_trace_path = run_folder / "agent_trace.json"
		try:
			_write_agent_trace(agent_trace_path, messages)
		except Exception as exc:
			logger.warning(f"[maintain] Failed to write agent trace: {exc}")
			agent_trace_path.write_text("[]", encoding="utf-8")

		counts = _record_change_counts(self.config, session_id)

		payload = {
			"context_db_path": str(self.config.context_db_path),
			"project_id": project_identity.project_id,
			"workspace_root": str(resolved_workspace_root),
			"run_folder": str(run_folder),
			"artifacts": {key: str(path) for key, path in artifact_paths.items()},
			"records_created": int(counts.get("create") or 0),
			"records_updated": int(counts.get("update") or 0) + int(counts.get("supersede") or 0),
			"records_archived": int(counts.get("archive") or 0),
			"cost_usd": 0.0,
		}
		return MaintainResultContract.model_validate(payload).model_dump(mode="json")

	# ------------------------------------------------------------------
	# Ask flow
	# ------------------------------------------------------------------

	def ask(
		self,
		prompt: str,
		session_id: str | None = None,
		project_ids: list[str] | None = None,
		repo_root: str | Path | None = None,
	) -> tuple[str, str, float]:
		"""Run one ask prompt. Returns (response, session_id, cost_usd)."""
		resolved_session_id = session_id or self.generate_session_id()
		resolved_repo_root = Path(repo_root).expanduser().resolve() if repo_root else Path(self._default_cwd or Path.cwd()).expanduser().resolve()
		project_identity = resolve_project_identity(resolved_repo_root)
		store = _store_for_config(self.config)
		store.register_project(project_identity)
		resolved_project_ids = project_ids or [project_identity.project_id]
		direct_answer = _direct_ask_answer(
			store=store,
			project_ids=resolved_project_ids,
			question=prompt,
		)
		if direct_answer is not None:
			return direct_answer, resolved_session_id, 0.0
		hints = format_ask_hints(hits=[], context_docs=[])

		def _primary_builder() -> Any:
			return build_pydantic_model("agent", config=self.config)

		def _call(model: Any) -> Any:
			return run_ask(
				context_db_path=self.config.context_db_path,
				project_identity=project_identity,
				project_ids=resolved_project_ids,
				session_id=resolved_session_id,
				model=model,
				question=prompt,
				hints=hints,
				request_limit=self.config.agent_role.max_iters_ask,
				return_messages=False,
			)

		result = self._run_with_fallback(
			flow="ask",
			callable_fn=_call,
			model_builders=[_primary_builder],
		)
		response_text = (result.answer or "").strip() or "(no response)"
		return response_text, resolved_session_id, 0.0
