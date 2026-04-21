"""Helpers for queue / daemon integration cases."""

from __future__ import annotations

from tests.conftest import QUEUE_EXPECTATIONS_DIR
from tests.integration.common_helpers import load_yaml_expectation


def load_queue_expectation(case_name: str) -> dict[str, object]:
    """Load one YAML expectation file for a queue integration case."""
    return load_yaml_expectation(QUEUE_EXPECTATIONS_DIR, case_name)
