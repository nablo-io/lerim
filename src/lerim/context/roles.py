"""Operational role metadata for durable context records."""

from __future__ import annotations

from enum import StrEnum
import json
from typing import Any


class RecordRole(StrEnum):
    """Operational role a context record can play for future agents."""

    GENERAL = "general"
    PROCEDURE = "procedure"
    GOTCHA = "gotcha"
    FAILURE_MODE = "failure_mode"
    ARTIFACT = "artifact"
    STATE_CHANGE = "state_change"
    EVAL_ASSET = "eval_asset"


ALLOWED_RECORD_ROLES = tuple(role.value for role in RecordRole)
DEFAULT_RECORD_ROLE = RecordRole.GENERAL.value
MAX_ROLE_PAYLOAD_CHARS = 4000
ROLE_PAYLOAD_KEYS: dict[str, tuple[str, ...]] = {
    RecordRole.PROCEDURE.value: ("trigger", "steps", "checks", "failure_cases"),
    RecordRole.GOTCHA.value: ("condition", "symptom", "avoid", "recover"),
    RecordRole.FAILURE_MODE.value: (
        "failure_step",
        "wrong_assumption",
        "correction",
        "prevention_check",
    ),
    RecordRole.ARTIFACT.value: ("artifact_type", "locator", "purpose", "status"),
    RecordRole.STATE_CHANGE.value: (
        "subject",
        "previous_state",
        "current_state",
        "applies_until",
    ),
    RecordRole.EVAL_ASSET.value: (
        "failure_pattern",
        "assertion",
        "fixture_hint",
        "evaluator_hint",
    ),
}


def normalize_record_role(value: Any) -> str:
    """Return a canonical operational record role."""
    text = str(value or DEFAULT_RECORD_ROLE).strip().lower().replace("-", "_")
    if text not in ALLOWED_RECORD_ROLES:
        raise ValueError(f"invalid_record_role:{value}")
    return text


def normalize_role_payload(*, role: str, value: Any) -> str | None:
    """Normalize a role payload to compact JSON text."""
    normalized_role = normalize_record_role(role)
    if normalized_role == DEFAULT_RECORD_ROLE or value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError("invalid_role_payload_json") from exc
    else:
        parsed = value
    if not isinstance(parsed, dict):
        raise ValueError("invalid_role_payload_type")
    allowed_keys = ROLE_PAYLOAD_KEYS.get(normalized_role, ())
    cleaned = {
        key: _normalize_payload_value(parsed.get(key))
        for key in allowed_keys
        if _normalize_payload_value(parsed.get(key)) is not None
    }
    if not cleaned:
        return None
    text = json.dumps(cleaned, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(text) > MAX_ROLE_PAYLOAD_CHARS:
        raise ValueError("role_payload_too_long")
    return text


def role_payload_search_text(role_payload: str | None) -> str:
    """Return human-readable search text from a normalized role payload."""
    text = str(role_payload or "").strip()
    if not text:
        return ""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return text[:MAX_ROLE_PAYLOAD_CHARS]
    parts = _payload_search_parts(payload)
    return "\n".join(parts)[:MAX_ROLE_PAYLOAD_CHARS]


def _normalize_payload_value(value: Any) -> Any:
    """Normalize one JSON-compatible payload value."""
    if value is None:
        return None
    if isinstance(value, str):
        text = " ".join(value.split())
        return text or None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, (list, tuple, set)):
        cleaned = [_normalize_payload_value(item) for item in value]
        cleaned = [item for item in cleaned if item is not None]
        return cleaned or None
    if isinstance(value, dict):
        cleaned = {
            str(key).strip(): _normalize_payload_value(item)
            for key, item in value.items()
            if str(key).strip()
        }
        cleaned = {key: item for key, item in cleaned.items() if item is not None}
        return cleaned or None
    text = " ".join(str(value).split())
    return text or None


def _payload_search_parts(value: Any, *, prefix: str = "") -> list[str]:
    """Flatten a normalized JSON value into compact search lines."""
    if isinstance(value, dict):
        parts: list[str] = []
        for key, item in value.items():
            key_prefix = f"{prefix}.{key}" if prefix else str(key)
            parts.extend(_payload_search_parts(item, prefix=key_prefix))
        return parts
    if isinstance(value, list):
        return [
            part
            for index, item in enumerate(value, start=1)
            for part in _payload_search_parts(item, prefix=f"{prefix}.{index}")
        ]
    text = str(value or "").strip()
    return [f"{prefix}: {text}"] if prefix and text else []
