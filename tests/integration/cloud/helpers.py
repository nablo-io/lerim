"""Helpers for cloud integration cases."""

from __future__ import annotations

from tests.conftest import CLOUD_EXPECTATIONS_DIR
from tests.integration.common_helpers import load_yaml_expectation


def load_cloud_expectation(case_name: str) -> dict[str, object]:
    """Load one YAML expectation file for a cloud integration case."""
    return load_yaml_expectation(CLOUD_EXPECTATIONS_DIR, case_name)
