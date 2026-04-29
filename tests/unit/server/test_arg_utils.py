"""Unit tests for CLI argument parsing helpers."""

from __future__ import annotations

import pytest

from lerim.server.api import parse_agent_filter, parse_csv, parse_duration_to_seconds


def test_parse_duration_seconds():
    """'30s' -> 30."""
    assert parse_duration_to_seconds("30s") == 30


def test_parse_duration_minutes():
    """'5m' -> 300."""
    assert parse_duration_to_seconds("5m") == 300


def test_parse_duration_hours():
    """'2h' -> 7200."""
    assert parse_duration_to_seconds("2h") == 7200


def test_parse_duration_days():
    """'1d' -> 86400."""
    assert parse_duration_to_seconds("1d") == 86400


def test_parse_duration_invalid_unit():
    """'5x' -> ValueError."""
    with pytest.raises(ValueError):
        parse_duration_to_seconds("5x")


def test_parse_duration_zero():
    """'0s' -> ValueError (must be > 0)."""
    with pytest.raises(ValueError):
        parse_duration_to_seconds("0s")


def test_parse_csv_normal():
    """'a,b,c' -> ['a','b','c']."""
    assert parse_csv("a,b,c") == ["a", "b", "c"]


def test_parse_csv_empty():
    """'' -> []."""
    assert parse_csv("") == []
    assert parse_csv(None) == []


def test_parse_csv_whitespace():
    """' a , b ' -> ['a','b']."""
    assert parse_csv(" a , b ") == ["a", "b"]


def test_parse_agent_filter_all():
    """'all' -> None."""
    assert parse_agent_filter("all") is None


def test_parse_agent_filter_specific():
    """'claude,codex' -> ['claude','codex']."""
    result = parse_agent_filter("claude,codex")
    assert result is not None
    assert "claude" in result
    assert "codex" in result
