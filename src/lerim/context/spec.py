"""Canonical Lerim context and finding specifications."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
import json
from typing import Any

from lerim.context.roles import (
    ALLOWED_RECORD_ROLES,
    DEFAULT_RECORD_ROLE,
    normalize_record_role,
    normalize_role_payload,
    role_payload_search_text,
)


class RecordKind(StrEnum):
    """Canonical durable-context record kinds."""

    DECISION = "decision"
    PREFERENCE = "preference"
    CONSTRAINT = "constraint"
    FACT = "fact"
    EPISODE = "episode"


class RecordStatus(StrEnum):
    """Canonical record lifecycle statuses."""

    ACTIVE = "active"
    ARCHIVED = "archived"


class RecordChangeKind(StrEnum):
    """Canonical record-version change kinds."""

    CREATE = "create"
    UPDATE = "update"
    ARCHIVE = "archive"
    SUPERSEDE = "supersede"


ALLOWED_KINDS = tuple(kind.value for kind in RecordKind)
ALLOWED_STATUSES = tuple(status.value for status in RecordStatus)
ALLOWED_CHANGE_KINDS = tuple(change_kind.value for change_kind in RecordChangeKind)
ALLOWED_ROLES = ALLOWED_RECORD_ROLES
MAX_RECORD_TITLE_CHARS = 120
MAX_EPISODE_BODY_CHARS = 1200
MAX_DURABLE_BODY_CHARS = 850
MAX_EPISODE_USER_INTENT_CHARS = 300
MAX_EPISODE_WHAT_HAPPENED_CHARS = 1000
MAX_EPISODE_OUTCOMES_CHARS = 300
RECORD_TYPED_FIELDS = (
    "decision",
    "why",
    "alternatives",
    "consequences",
    "user_intent",
    "what_happened",
    "outcomes",
)
SEARCH_TEXT_FIELDS = ("source_profile", "title", "body", *RECORD_TYPED_FIELDS)


@dataclass(frozen=True)
class TypedFieldSpec:
    """Canonical constraints for one typed record field."""

    name: str
    required: bool = False
    max_chars: int | None = None
    too_long_code: str | None = None


@dataclass(frozen=True)
class RecordKindSpec:
    """Canonical constraints for one durable record kind."""

    name: str
    body_max_chars: int
    body_too_long_code: str
    typed_fields: tuple[TypedFieldSpec, ...] = ()
    required_error_code: str | None = None
    requires_session_id: bool = False

    @property
    def typed_field_names(self) -> tuple[str, ...]:
        """Return typed field names for this record kind."""
        return tuple(field.name for field in self.typed_fields)

    @property
    def required_fields(self) -> tuple[str, ...]:
        """Return required typed field names for this record kind."""
        return tuple(field.name for field in self.typed_fields if field.required)


RECORD_KIND_SPECS = {
    RecordKind.DECISION.value: RecordKindSpec(
        name=RecordKind.DECISION.value,
        body_max_chars=MAX_DURABLE_BODY_CHARS,
        body_too_long_code="record_body_too_long",
        typed_fields=(
            TypedFieldSpec("decision", required=True),
            TypedFieldSpec("why", required=True),
            TypedFieldSpec("alternatives"),
            TypedFieldSpec("consequences"),
        ),
        required_error_code="decision_requires_decision_and_why",
    ),
    RecordKind.PREFERENCE.value: RecordKindSpec(
        name=RecordKind.PREFERENCE.value,
        body_max_chars=MAX_DURABLE_BODY_CHARS,
        body_too_long_code="record_body_too_long",
    ),
    RecordKind.CONSTRAINT.value: RecordKindSpec(
        name=RecordKind.CONSTRAINT.value,
        body_max_chars=MAX_DURABLE_BODY_CHARS,
        body_too_long_code="record_body_too_long",
    ),
    RecordKind.FACT.value: RecordKindSpec(
        name=RecordKind.FACT.value,
        body_max_chars=MAX_DURABLE_BODY_CHARS,
        body_too_long_code="record_body_too_long",
    ),
    RecordKind.EPISODE.value: RecordKindSpec(
        name=RecordKind.EPISODE.value,
        body_max_chars=MAX_EPISODE_BODY_CHARS,
        body_too_long_code="episode_body_too_long",
        typed_fields=(
            TypedFieldSpec(
                "user_intent",
                required=True,
                max_chars=MAX_EPISODE_USER_INTENT_CHARS,
                too_long_code="episode_user_intent_too_long",
            ),
            TypedFieldSpec(
                "what_happened",
                required=True,
                max_chars=MAX_EPISODE_WHAT_HAPPENED_CHARS,
                too_long_code="episode_what_happened_too_long",
            ),
            TypedFieldSpec(
                "outcomes",
                max_chars=MAX_EPISODE_OUTCOMES_CHARS,
                too_long_code="episode_outcomes_too_long",
            ),
        ),
        required_error_code="episode_requires_user_intent_and_what_happened",
        requires_session_id=True,
    ),
}
DURABLE_RECORD_KINDS = tuple(
    kind_name for kind_name in ALLOWED_KINDS if kind_name != RecordKind.EPISODE.value
)

def _utc_now() -> str:
    """Return current UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


def _normalize_optional_text(value: Any) -> str | None:
    """Normalize optional text fields to stripped strings or None."""
    text = str(value or "").strip()
    return text or None


def _normalize_reference_list(value: Any) -> str | None:
    """Normalize optional evidence references into compact JSON text."""
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            items = [text]
        else:
            items = parsed if isinstance(parsed, list) else [parsed]
    elif isinstance(value, (list, tuple, set)):
        items = list(value)
    else:
        items = [value]

    normalized = [str(item).strip() for item in items if str(item).strip()]
    if not normalized:
        return None
    return json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))


def normalize_record_kind(value: Any) -> str:
    """Normalize one record kind candidate."""
    return str(value or "").strip().lower()


def normalize_record_status(value: Any, default: str = "active") -> str:
    """Normalize one record status candidate."""
    return str(value or default).strip().lower()


def format_durable_record_kinds() -> str:
    """Return one human-readable durable-kind description."""
    if not DURABLE_RECORD_KINDS:
        return ""
    if len(DURABLE_RECORD_KINDS) == 1:
        return DURABLE_RECORD_KINDS[0]
    head = ", ".join(DURABLE_RECORD_KINDS[:-1])
    return f"{head}, or {DURABLE_RECORD_KINDS[-1]}"


def record_validation_message(code: str) -> str | None:
    """Return one user-facing retry message for a validation code."""
    messages = {
        "title_required": "Every record needs a non-empty title.",
        "body_required": "Every record needs a non-empty body.",
        "title_too_long": (
            f"Title is too long. Use one short specific context title under "
            f"{MAX_RECORD_TITLE_CHARS} characters."
        ),
        "decision_requires_decision_and_why": (
            "Decision records need both `decision` and `why`. "
            "If you cannot supply both, create a `fact` instead."
        ),
        "episode_requires_session_id": (
            "Episode records must stay tied to the current session."
        ),
        "episode_requires_user_intent_and_what_happened": (
            "Episode records need both `user_intent` and `what_happened`."
        ),
        "duplicate_episode_for_session": (
            "This session already has an episode record. Do not create another episode. "
            "Continue with durable records, or update the existing episode only if you truly "
            "need to improve it."
        ),
        "episode_body_too_long": (
            "Episode body is too long. Keep a compact session recap."
        ),
        "episode_user_intent_too_long": (
            "user_intent is too long. Compress it to one short sentence."
        ),
        "episode_what_happened_too_long": (
            "what_happened is too long. Keep only the essential session outcome."
        ),
        "episode_outcomes_too_long": (
            "outcomes is too long. Keep only the lasting result."
        ),
        "record_body_too_long": (
            "Durable record body is too long. Keep only the reusable "
            "rule/decision/fact and why it matters."
        ),
        "invalid_record_role": "Record role must be one of the canonical operational roles.",
        "invalid_role_payload_json": "Record role payload must be valid JSON.",
        "invalid_role_payload_type": "Record role payload must be a JSON object.",
        "role_payload_too_long": "Record role payload is too long. Keep only reusable structured fields.",
    }
    return messages.get(str(code or "").strip())


def normalize_record_payload(
    *,
    kind: Any,
    title: Any,
    body: Any,
    status: Any,
    source_session_id: Any,
    created_at: Any,
    updated_at: Any,
    valid_from: Any,
    valid_until: Any,
    superseded_by_record_id: Any,
    decision: Any,
    why: Any,
    alternatives: Any,
    consequences: Any,
    user_intent: Any,
    what_happened: Any,
    outcomes: Any,
    source_event_refs: Any = None,
    evidence_refs: Any = None,
    record_role: Any = DEFAULT_RECORD_ROLE,
    role_payload: Any = None,
) -> dict[str, Any]:
    """Normalize and validate one record payload against the shared spec."""
    kind_text = normalize_record_kind(kind)
    if kind_text not in ALLOWED_KINDS:
        raise ValueError(f"invalid_kind:{kind}")
    status_text = normalize_record_status(status)
    if status_text not in ALLOWED_STATUSES:
        raise ValueError(f"invalid_status:{status}")
    role_text = normalize_record_role(record_role)
    normalized_role_payload = normalize_role_payload(
        role=role_text,
        value=role_payload,
    )
    title_text = str(title or "").strip()
    body_text = str(body or "").strip()
    if not title_text:
        raise ValueError("title_required")
    if not body_text:
        raise ValueError("body_required")
    if len(title_text) > MAX_RECORD_TITLE_CHARS:
        raise ValueError("title_too_long")

    now = _utc_now()
    created_at_text = str(created_at or now).strip()
    updated_at_text = str(updated_at or now).strip()
    payload = {
        "kind": kind_text,
        "title": title_text,
        "body": body_text,
        "status": status_text,
        "source_session_id": _normalize_optional_text(source_session_id),
        "created_at": created_at_text,
        "updated_at": updated_at_text,
        "valid_from": str(valid_from or created_at_text).strip(),
        "valid_until": _normalize_optional_text(valid_until),
        "superseded_by_record_id": _normalize_optional_text(superseded_by_record_id),
        "decision": _normalize_optional_text(decision),
        "why": _normalize_optional_text(why),
        "alternatives": _normalize_optional_text(alternatives),
        "consequences": _normalize_optional_text(consequences),
        "user_intent": _normalize_optional_text(user_intent),
        "what_happened": _normalize_optional_text(what_happened),
        "outcomes": _normalize_optional_text(outcomes),
        "source_event_refs": _normalize_reference_list(source_event_refs),
        "evidence_refs": _normalize_reference_list(evidence_refs),
        "record_role": role_text,
        "role_payload": normalized_role_payload,
    }
    if payload["status"] == "archived" and not payload["valid_until"]:
        payload["valid_until"] = payload["updated_at"]

    kind_spec = RECORD_KIND_SPECS[kind_text]
    if len(body_text) > kind_spec.body_max_chars:
        raise ValueError(kind_spec.body_too_long_code)

    typed_fields_by_name = {field.name: field for field in kind_spec.typed_fields}
    for field_name, field_spec in typed_fields_by_name.items():
        field_value = payload[field_name]
        if field_value and field_spec.max_chars is not None:
            if len(field_value) > field_spec.max_chars:
                raise ValueError(field_spec.too_long_code or f"{field_name}_too_long")

    if kind_spec.requires_session_id and not payload["source_session_id"]:
        raise ValueError("episode_requires_session_id")

    if kind_spec.required_fields and any(
        not payload[field_name] for field_name in kind_spec.required_fields
    ):
        if kind_spec.required_error_code:
            raise ValueError(kind_spec.required_error_code)

    allowed_typed_fields = set(kind_spec.typed_field_names)
    for field_name in RECORD_TYPED_FIELDS:
        if field_name not in allowed_typed_fields:
            payload[field_name] = None
    return payload


def record_search_text(payload: dict[str, Any], *, index_text: str | None = None) -> str:
    """Build canonical search text from one normalized record payload."""
    parts: list[str] = [f"kind: {payload['kind']}"]
    role = str(payload.get("record_role") or DEFAULT_RECORD_ROLE).strip()
    if role:
        parts.append(f"role: {role}")
    for field_name in SEARCH_TEXT_FIELDS:
        text = str(payload.get(field_name) or "").strip()
        if text:
            parts.append(f"{field_name}: {text}")
    role_text = role_payload_search_text(payload.get("role_payload"))
    if role_text:
        parts.append(f"role_payload:\n{role_text}")
    extra = str(index_text or "").strip()
    if extra:
        parts.append(f"index_text: {extra}")
    return "\n".join(parts)
