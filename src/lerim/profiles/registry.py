"""Signal-pack registry loaded from bundled YAML profiles."""

from __future__ import annotations

from functools import lru_cache
from importlib import resources
from typing import Any

import yaml

from lerim.profiles.base import SignalPack

DEFAULT_SIGNAL_PACK_ID = "coding"
_PROFILE_FILES = ("coding.yaml", "support.yaml", "ops.yaml")


def list_signal_packs() -> list[SignalPack]:
    """Return all bundled signal packs."""
    return sorted(_load_signal_packs().values(), key=lambda pack: pack.id)


def get_signal_pack(profile: str | None) -> SignalPack:
    """Return a signal pack, falling back to the generic coding wedge."""
    profile_id = _raw_profile_id(profile)
    packs = _load_signal_packs()
    return packs.get(profile_id) or packs[DEFAULT_SIGNAL_PACK_ID]


def normalize_signal_pack_id(profile: str | None) -> str:
    """Return the canonical bundled signal-pack id for a requested profile."""
    return get_signal_pack(profile).id


def format_signal_pack_context(profile: str | None) -> str:
    """Render a compact prompt context block for a source profile."""
    pack = get_signal_pack(profile)
    sections = [
        ("Focus rules", pack.focus_rules),
        ("Reject as noise", pack.reject_as_noise),
        ("Evidence rules", pack.evidence_rules),
        ("Scope rules", pack.scope_rules),
    ]
    lines = [
        f"id: {pack.id}",
        f"display_name: {pack.display_name}",
        f"description: {pack.description}",
    ]
    for title, values in sections:
        lines.append(f"{title}:")
        lines.extend(f"- {value}" for value in values)
    return "\n".join(lines)


@lru_cache(maxsize=1)
def _load_signal_packs() -> dict[str, SignalPack]:
    """Load bundled YAML signal packs."""
    packs: dict[str, SignalPack] = {}
    for filename in _PROFILE_FILES:
        text = resources.files("lerim.profiles").joinpath(filename).read_text()
        payload = yaml.safe_load(text) or {}
        pack = _pack_from_payload(payload)
        packs[pack.id] = pack
    return packs


def _pack_from_payload(payload: dict[str, Any]) -> SignalPack:
    """Normalize one loaded YAML payload."""
    return SignalPack(
        id=_required_text(payload, "id"),
        display_name=_required_text(payload, "display_name"),
        description=_required_text(payload, "description"),
        focus_rules=_text_tuple(payload.get("focus_rules")),
        reject_as_noise=_text_tuple(payload.get("reject_as_noise")),
        evidence_rules=_text_tuple(payload.get("evidence_rules")),
        scope_rules=_text_tuple(payload.get("scope_rules")),
    )


def _raw_profile_id(profile: str | None) -> str:
    """Normalize user-provided profile text before registry lookup."""
    return str(profile or DEFAULT_SIGNAL_PACK_ID).strip().lower() or DEFAULT_SIGNAL_PACK_ID


def _required_text(payload: dict[str, Any], key: str) -> str:
    """Read a required non-empty string field."""
    text = str(payload.get(key) or "").strip()
    if not text:
        raise ValueError(f"invalid_signal_pack:{key}")
    return text


def _text_tuple(value: Any) -> tuple[str, ...]:
    """Normalize YAML scalar/list fields into a tuple of strings."""
    if value is None:
        return ()
    if isinstance(value, str):
        items = [value]
    else:
        items = list(value)
    return tuple(str(item).strip() for item in items if str(item).strip())
