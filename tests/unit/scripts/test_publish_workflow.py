"""Tests for the release publishing workflow shape."""

from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[3]
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "publish.yml"


def _workflow() -> dict[str, object]:
    """Load the publish workflow."""
    payload = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def _jobs() -> dict[str, dict[str, object]]:
    """Return workflow jobs keyed by job id."""
    jobs = _workflow()["jobs"]
    assert isinstance(jobs, dict)
    return jobs


def _job_step(job: dict[str, object], name: str) -> dict[str, object]:
    """Return one named job step."""
    steps = job["steps"]
    assert isinstance(steps, list)
    for step in steps:
        if isinstance(step, dict) and step.get("name") == name:
            return step
    raise AssertionError(f"missing workflow step {name!r}")


def test_pypi_publish_waits_for_docker_release_validation() -> None:
    """PyPI publish must wait for both runtime and multi-platform Docker validation."""
    publish = _jobs()["publish"]

    assert set(publish["needs"]) >= {"build", "docker-smoke", "docker-multi-platform-build"}


def test_docker_push_is_approval_gated_with_pypi_publish() -> None:
    """GHCR pushes mutate release tags and should use the release approval boundary."""
    jobs = _jobs()

    assert jobs["publish"]["environment"] == "pypi"
    assert jobs["docker-push"]["environment"] == "pypi"


def test_multi_platform_validation_matches_docker_push_platforms() -> None:
    """The pre-PyPI Docker validation should build the same platform set as publish."""
    jobs = _jobs()
    validate_step = _job_step(
        jobs["docker-multi-platform-build"],
        "Build release Docker image without publishing",
    )
    push_step = _job_step(jobs["docker-push"], "Build and push Docker image")
    validate_with = validate_step["with"]
    push_with = push_step["with"]
    assert isinstance(validate_with, dict)
    assert isinstance(push_with, dict)

    assert validate_with["push"] is False
    assert validate_with["platforms"] == push_with["platforms"]
    assert "latest" in validate_with["tags"]
    assert "latest" in push_with["tags"]


def test_docker_release_uses_renamed_ghcr_package() -> None:
    """Release images should publish under the renamed repository package."""
    jobs = _jobs()
    validate_step = _job_step(
        jobs["docker-multi-platform-build"],
        "Build release Docker image without publishing",
    )
    push_step = _job_step(jobs["docker-push"], "Build and push Docker image")

    tags = "\n".join(
        [
            str(validate_step["with"]["tags"]),
            str(push_step["with"]["tags"]),
        ]
    )

    old_package = "ghcr.io/lerim-dev/" + "lerim-cli:"
    assert "ghcr.io/nablo-io/lerim:" in tags
    assert old_package not in tags
