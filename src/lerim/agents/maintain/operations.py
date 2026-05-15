"""Validated maintain mutation application."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lerim.context import ContextStore, ProjectIdentity
from lerim.context.spec import RECORD_TYPED_FIELDS, normalize_record_kind, normalize_record_status


@dataclass(frozen=True)
class ActionApplicationSummary:
    """Result of applying a maintain action plan."""

    observations: list[dict[str, Any]]
    records_created: int = 0
    records_updated: int = 0
    records_archived: int = 0
    applied_actions: int = 0


def apply_maintain_action_plans(
    *,
    context_db_path: Path,
    project_identity: ProjectIdentity,
    session_id: str,
    action_plans: list[dict[str, Any]],
    evidence_record_ids: set[str],
) -> ActionApplicationSummary:
    """Validate and apply BAML-proposed maintain actions."""
    store = ContextStore(context_db_path)
    store.initialize()
    store.register_project(project_identity)
    current_records = _load_evidence_records(
        store=store,
        project_identity=project_identity,
        evidence_record_ids=evidence_record_ids,
    )
    observations: list[dict[str, Any]] = []
    touched_record_ids: set[str] = set()
    counts = {"records_created": 0, "records_updated": 0, "records_archived": 0}
    applied_actions = 0

    for raw_action in _iter_actions(action_plans):
        action = _model_payload(raw_action)
        action_type = _clean_action_type(action.get("action_type"))
        record_id = str(action.get("record_id") or "").strip()
        if action_type == "noop":
            continue
        validation_error = _validate_action(
            action=action,
            action_type=action_type,
            record_id=record_id,
            evidence_record_ids=evidence_record_ids,
            current_records=current_records,
            touched_record_ids=touched_record_ids,
        )
        if validation_error:
            observations.append(_observation("apply_maintain_action", False, validation_error, action))
            continue
        try:
            if action_type == "archive":
                store.archive_record(
                    record_id=record_id,
                    session_id=session_id,
                    project_ids=[project_identity.project_id],
                    reason=str(action.get("reason") or "").strip() or "baml_maintain_archive",
                )
                counts["records_archived"] += 1
            elif action_type == "supersede":
                replacement_record_id = str(action.get("replacement_record_id") or "").strip()
                store.supersede_record(
                    record_id=record_id,
                    session_id=session_id,
                    project_ids=[project_identity.project_id],
                    replacement_record_id=replacement_record_id,
                    reason=str(action.get("reason") or "").strip() or "baml_maintain_supersede",
                    valid_until=str(action.get("valid_until") or "").strip() or None,
                )
                counts["records_updated"] += 1
            elif action_type == "revise":
                changes = _changes_from_patch(action.get("patch") or {})
                store.update_record(
                    record_id=record_id,
                    session_id=session_id,
                    project_ids=[project_identity.project_id],
                    changes=changes,
                    change_reason=str(action.get("reason") or "").strip() or "baml_maintain_revise",
                )
                counts["records_updated"] += 1
            else:
                observations.append(
                    _observation(
                        "apply_maintain_action",
                        False,
                        f"unsupported_action_type:{action_type}",
                        action,
                    )
                )
                continue
        except Exception as exc:
            observations.append(
                _observation(
                    "apply_maintain_action",
                    False,
                    f"{type(exc).__name__}: {exc}",
                    action,
                )
            )
            continue
        touched_record_ids.add(record_id)
        applied_actions += 1
        observations.append(
            _observation(
                "apply_maintain_action",
                True,
                f"{action_type}:{record_id}",
                action,
            )
        )

    return ActionApplicationSummary(
        observations=observations,
        records_created=counts["records_created"],
        records_updated=counts["records_updated"],
        records_archived=counts["records_archived"],
        applied_actions=applied_actions,
    )


def summarize_application(summary: ActionApplicationSummary) -> str:
    """Return a compact maintain completion summary."""
    if summary.applied_actions == 0:
        return "Maintain completed with no record changes."
    parts = []
    if summary.records_updated:
        parts.append(f"{summary.records_updated} updated")
    if summary.records_archived:
        parts.append(f"{summary.records_archived} archived")
    if summary.records_created:
        parts.append(f"{summary.records_created} created")
    return "Maintain completed: " + ", ".join(parts) + "."


def _iter_actions(action_plans: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Flatten BAML action plans into action dictionaries."""
    actions: list[dict[str, Any]] = []
    for plan in action_plans:
        payload = _model_payload(plan)
        for action in payload.get("actions") or []:
            actions.append(_model_payload(action))
    return actions


def _validate_action(
    *,
    action: dict[str, Any],
    action_type: str,
    record_id: str,
    evidence_record_ids: set[str],
    current_records: dict[str, dict[str, Any]],
    touched_record_ids: set[str],
) -> str | None:
    """Return a validation error for unsafe actions, or None."""
    if not record_id:
        return "missing_record_id"
    if record_id not in evidence_record_ids:
        return f"unfetched_record:{record_id}"
    if record_id in touched_record_ids:
        return f"duplicate_action_for_record:{record_id}"
    if action_type == "supersede":
        replacement_record_id = str(action.get("replacement_record_id") or "").strip()
        if not replacement_record_id:
            return "missing_replacement_record_id"
        if replacement_record_id not in evidence_record_ids:
            return f"unfetched_replacement_record:{replacement_record_id}"
        if replacement_record_id == record_id:
            return f"self_supersede:{record_id}"
    if action_type == "revise" and not isinstance(action.get("patch"), dict):
        return "missing_revision_patch"
    if action_type == "revise":
        current_kind = normalize_record_kind(
            str((current_records.get(record_id) or {}).get("kind") or "")
        )
        patch_kind = normalize_record_kind(
            str((_model_payload(action.get("patch") or {})).get("kind") or "")
        )
        if current_kind and patch_kind and current_kind != patch_kind:
            return f"kind_change_not_allowed:{record_id}:{current_kind}->{patch_kind}"
    return None


def _load_evidence_records(
    *,
    store: ContextStore,
    project_identity: ProjectIdentity,
    evidence_record_ids: set[str],
) -> dict[str, dict[str, Any]]:
    """Fetch action evidence records for invariant checks."""
    records: dict[str, dict[str, Any]] = {}
    for record_id in evidence_record_ids:
        record = store.fetch_record(
            record_id,
            project_ids=[project_identity.project_id],
            include_versions=False,
        )
        if record is not None:
            records[str(record["record_id"])] = record
    return records


def _changes_from_patch(patch: dict[str, Any]) -> dict[str, Any]:
    """Convert a BAML record patch into ContextStore update changes."""
    payload = _model_payload(patch)
    changes = {
        "kind": normalize_record_kind(str(payload.get("kind") or "")),
        "title": str(payload.get("title") or "").strip(),
        "body": str(payload.get("body") or "").strip(),
        "status": normalize_record_status(str(payload.get("status") or "active")),
    }
    for key in ("valid_from", "valid_until", "superseded_by_record_id"):
        value = str(payload.get(key) or "").strip()
        if value:
            changes[key] = value
    for field_name in RECORD_TYPED_FIELDS:
        value = payload.get(field_name)
        changes[field_name] = str(value).strip() if value is not None and str(value).strip() else None
    return changes


def _clean_action_type(value: Any) -> str:
    """Normalize generated enum/string action type values."""
    enum_value = getattr(value, "value", None)
    text = str(enum_value if enum_value is not None else value or "").strip().lower()
    return text or "noop"


def _model_payload(value: Any) -> dict[str, Any]:
    """Convert generated BAML objects into plain dictionaries."""
    if hasattr(value, "model_dump"):
        return _plain_value(value.model_dump(exclude_none=True))
    if isinstance(value, dict):
        return _plain_value({key: item for key, item in value.items() if item is not None})
    if value is None:
        return {}
    return _plain_value(getattr(value, "__dict__", {}))


def _plain_value(value: Any) -> Any:
    """Convert enum-ish values recursively into JSON-like values."""
    enum_value = getattr(value, "value", None)
    if enum_value is not None:
        return enum_value
    if isinstance(value, dict):
        return {key: _plain_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_plain_value(item) for item in value]
    return value


def _observation(action: str, ok: bool, content: str, args: dict[str, Any]) -> dict[str, Any]:
    """Build one maintain graph observation."""
    return {
        "action": action,
        "ok": ok,
        "content": content,
        "args": json.loads(json.dumps(args, ensure_ascii=True, default=str)),
        "done": False,
        "completion_summary": "",
    }


def utc_now() -> str:
    """Return current UTC timestamp for tests and valid-until defaults."""
    return datetime.now(timezone.utc).isoformat()
