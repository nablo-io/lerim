"""Typed signal-pack model."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SignalPack:
    """Workflow-specific priorities for the generic context compiler."""

    id: str
    display_name: str
    description: str
    focus_rules: tuple[str, ...]
    reject_as_noise: tuple[str, ...]
    evidence_rules: tuple[str, ...]
    scope_rules: tuple[str, ...]
