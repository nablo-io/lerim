"""Signal-pack registry loaded from bundled and user-registered YAML profiles."""

from __future__ import annotations

from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Any

import yaml

from lerim.profiles.base import SignalPack

DEFAULT_SIGNAL_PACK_ID = "coding"
_PROFILE_FILES = (
    "coding.yaml",
    "generic.yaml",
    "support.yaml",
    "ops.yaml",
    "research.yaml",
    "compliance.yaml",
)


def list_signal_packs() -> list[SignalPack]:
    """Return all bundled and user-registered signal packs."""
    return sorted(_load_signal_packs().values(), key=lambda pack: pack.id)


def get_signal_pack(profile: str | None) -> SignalPack:
    """Return a signal pack, falling back to the generic coding pack."""
    profile_id = _raw_profile_id(profile)
    packs = _load_signal_packs()
    return packs.get(profile_id) or packs[DEFAULT_SIGNAL_PACK_ID]


def normalize_signal_pack_id(profile: str | None) -> str:
    """Return the canonical signal-pack id for a requested profile."""
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


def _load_signal_packs() -> dict[str, SignalPack]:
    """Load bundled YAML signal packs plus user-registered custom packs."""
    packs = dict(_load_bundled_signal_packs())
    for configured_id, path in _custom_profile_paths().items():
        pack = load_signal_pack_file(Path(path).expanduser().resolve())
        if configured_id != pack.id:
            raise ValueError(
                "registered profile id must match YAML id: "
                f"profiles.{configured_id} points to {pack.id}"
            )
        if pack.id in packs:
            raise ValueError(
                f"custom profile '{pack.id}' conflicts with a bundled profile id"
            )
        packs[pack.id] = pack
    return packs


@lru_cache(maxsize=1)
def _load_bundled_signal_packs() -> dict[str, SignalPack]:
    """Load bundled YAML signal packs."""
    packs: dict[str, SignalPack] = {}
    for filename in _PROFILE_FILES:
        text = resources.files("lerim.profiles").joinpath(filename).read_text()
        payload = yaml.safe_load(text) or {}
        pack = _pack_from_payload(payload, source="bundled")
        packs[pack.id] = pack
    return packs


def bundled_signal_pack_ids() -> frozenset[str]:
    """Return ids reserved by bundled signal packs."""
    return frozenset(_load_bundled_signal_packs())


def load_signal_pack_file(path: Path) -> SignalPack:
    """Load and validate one custom profile YAML file."""
    if not path.exists():
        raise ValueError(f"profile YAML file does not exist: {path}")
    if not path.is_file():
        raise ValueError(f"profile YAML path is not a file: {path}")
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"profile YAML must be a mapping: {path}")
    return _pack_from_payload(payload, source="custom", path=path)


def reload_signal_packs() -> None:
    """Clear cached bundled signal packs."""
    _load_bundled_signal_packs.cache_clear()


def _pack_from_payload(
    payload: dict[str, Any],
    *,
    source: str,
    path: Path | None = None,
) -> SignalPack:
    """Normalize one loaded YAML payload."""
    return SignalPack(
        id=_required_id(payload),
        display_name=_required_text(payload, "display_name"),
        description=_required_text(payload, "description"),
        focus_rules=_required_text_tuple(payload, "focus_rules"),
        reject_as_noise=_required_text_tuple(payload, "reject_as_noise"),
        evidence_rules=_required_text_tuple(payload, "evidence_rules"),
        scope_rules=_required_text_tuple(payload, "scope_rules"),
        source=source,
        path=str(path) if path else "",
    )


def _raw_profile_id(profile: str | None) -> str:
    """Normalize user-provided profile text before registry lookup."""
    return str(profile or DEFAULT_SIGNAL_PACK_ID).strip().lower() or DEFAULT_SIGNAL_PACK_ID


def _custom_profile_paths() -> dict[str, str]:
    """Return custom profile paths from the active Lerim config."""
    try:
        from lerim.config.settings import get_config
    except ImportError:
        return {}
    profiles = get_config().profiles
    return {
        _raw_profile_id(profile_id): str(path).strip()
        for profile_id, path in profiles.items()
        if str(path).strip()
    }


def _required_id(payload: dict[str, Any]) -> str:
    """Read and validate a profile id."""
    text = _required_text(payload, "id")
    lowered = text.lower()
    if text != lowered:
        raise ValueError("invalid_signal_pack:id must be lowercase")
    valid = all(char.isalnum() or char in {"-", "_"} for char in text)
    if not valid:
        raise ValueError("invalid_signal_pack:id must use letters, numbers, '-' or '_'")
    return text


def _required_text(payload: dict[str, Any], key: str) -> str:
    """Read a required non-empty string field."""
    text = str(payload.get(key) or "").strip()
    if not text:
        raise ValueError(f"invalid_signal_pack:{key}")
    return text


def _required_text_tuple(payload: dict[str, Any], key: str) -> tuple[str, ...]:
    """Read a required non-empty scalar/list field as strings."""
    values = _text_tuple(payload.get(key), key=key)
    if not values:
        raise ValueError(f"invalid_signal_pack:{key}")
    return values


def _text_tuple(value: Any, *, key: str) -> tuple[str, ...]:
    """Normalize YAML scalar/list fields into a tuple of strings."""
    if value is None:
        return ()
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, list):
        items = value
    else:
        raise ValueError(f"invalid_signal_pack:{key} must be a string or list")
    return tuple(str(item).strip() for item in items if str(item).strip())
