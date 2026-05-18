"""Tests for bundled source-profile registry behavior."""

from __future__ import annotations

from lerim.profiles import (
    format_signal_pack_context,
    get_signal_pack,
    normalize_signal_pack_id,
)


def test_normalize_signal_pack_id_uses_canonical_bundled_ids() -> None:
    """Profile lookup returns exact bundled ids and falls back without guessing."""
    assert normalize_signal_pack_id("support") == "support"
    assert normalize_signal_pack_id("SUPPORT") == "support"
    assert normalize_signal_pack_id("support handoff trace") == "coding"
    assert normalize_signal_pack_id(None) == "coding"


def test_format_signal_pack_context_excludes_eval_schema() -> None:
    """Runtime prompt context should not include eval-only labels."""
    rendered = format_signal_pack_context("ops")

    assert "Evaluation gold schema" not in rendered
    assert "Focus rules:" in rendered
    assert "Output cards:" not in rendered
    assert "card_type" not in rendered


def test_signal_pack_focus_is_guidance_not_output_taxonomy() -> None:
    """Profiles should guide extraction without defining product card outputs."""
    for profile in ("coding", "support", "ops"):
        pack = get_signal_pack(profile)

        assert pack.focus_rules
        assert not hasattr(pack, "output_cards")
        assert not hasattr(pack, "signal_types")
