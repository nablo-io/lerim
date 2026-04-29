"""Helpers for CLI surface e2e cases."""

from __future__ import annotations

from tests.conftest import CLI_SURFACE_EXPECTATIONS_DIR
from tests.integration.common_helpers import load_yaml_expectation


def load_cli_surface_expectation(case_name: str) -> dict[str, object]:
    """Load one YAML expectation file for a CLI surface case."""
    return load_yaml_expectation(CLI_SURFACE_EXPECTATIONS_DIR, case_name)
