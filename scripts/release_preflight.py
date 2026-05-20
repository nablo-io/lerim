"""Validate release metadata before publishing Lerim artifacts."""

from __future__ import annotations

import argparse
from datetime import date
import json
import os
import re
import sys
import tomllib
import urllib.error
import urllib.request
from pathlib import Path
from typing import Sequence


PYPI_VERSION_URL = "https://pypi.org/pypi/{package}/{version}/json"
PYPI_PROJECT_URL = "https://pypi.org/pypi/{package}/json"
FINAL_RELEASE_VERSION_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse release preflight command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Validate tag, pyproject, changelog, and PyPI release state.",
    )
    parser.add_argument(
        "--tag",
        help=(
            "Release tag such as v0.3.0. When neither --tag nor --version is "
            "provided, defaults to GITHUB_REF_NAME if it looks like a release tag."
        ),
    )
    parser.add_argument(
        "--version",
        help="Expected package version without a leading v.",
    )
    parser.add_argument(
        "--pyproject",
        type=Path,
        default=Path("pyproject.toml"),
        help="Path to pyproject.toml.",
    )
    parser.add_argument(
        "--changelog",
        type=Path,
        default=Path("CHANGELOG.md"),
        help="Path to CHANGELOG.md.",
    )
    parser.add_argument(
        "--package",
        help="PyPI package name. Defaults to project.name from pyproject.toml.",
    )
    parser.add_argument(
        "--skip-pypi",
        action="store_true",
        help="Skip the PyPI duplicate-release check.",
    )
    parser.add_argument(
        "--pypi-timeout",
        type=float,
        default=8.0,
        help="Timeout in seconds for the PyPI duplicate-release check.",
    )
    return parser.parse_args(argv)


def default_release_tag() -> str | None:
    """Return the GitHub tag ref when the current ref is a release tag."""
    ref_name = os.getenv("GITHUB_REF_NAME")
    if ref_name and ref_name.startswith("v"):
        return ref_name
    return None


def tag_to_version(tag: str) -> str:
    """Return the semantic version encoded by a release tag."""
    if not tag.startswith("v"):
        raise SystemExit(f"release tag must start with v, got {tag!r}")
    version = tag[1:]
    if not version:
        raise SystemExit("release tag must include a version after v")
    if not FINAL_RELEASE_VERSION_RE.fullmatch(version):
        raise SystemExit(
            "release tag must be a final SemVer tag like v0.3.0; "
            f"got {tag!r}"
        )
    return version


def project_metadata(pyproject_path: Path) -> dict[str, str]:
    """Read the package name and version from pyproject.toml."""
    data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    project = data.get("project")
    if not isinstance(project, dict):
        raise SystemExit(f"{pyproject_path} is missing [project] metadata")
    name = project.get("name")
    version = project.get("version")
    if not isinstance(name, str) or not name:
        raise SystemExit(f"{pyproject_path} is missing project.name")
    if not isinstance(version, str) or not version:
        raise SystemExit(f"{pyproject_path} is missing project.version")
    return {"name": name, "version": version}


def expected_release_version(tag: str | None, version: str | None) -> str:
    """Resolve the expected release version from a tag or explicit version."""
    tag_version = tag_to_version(tag) if tag else None
    if tag_version and version and tag_version != version:
        raise SystemExit(f"tag version {tag_version!r} does not match --version {version!r}")
    expected = version or tag_version
    if not expected:
        raise SystemExit("provide --tag, --version, or set GITHUB_REF_NAME")
    if not FINAL_RELEASE_VERSION_RE.fullmatch(expected):
        raise SystemExit(
            "release version must be a final SemVer version like 0.3.0; "
            f"got {expected!r}"
        )
    return expected


def final_version_tuple(version: str) -> tuple[int, int, int] | None:
    """Return the sortable tuple for final SemVer versions only."""
    match = FINAL_RELEASE_VERSION_RE.fullmatch(version)
    if not match:
        return None
    return tuple(int(part) for part in match.groups())


def changelog_release_date(changelog_path: Path, version: str) -> date | None:
    """Return the parsed changelog release date for the version."""
    changelog = changelog_path.read_text(encoding="utf-8")
    pattern = rf"^## \[{re.escape(version)}\] - (?P<date>\d{{4}}-\d{{2}}-\d{{2}})$"
    match = re.search(pattern, changelog, flags=re.MULTILINE)
    if not match:
        return None
    try:
        return date.fromisoformat(match.group("date"))
    except ValueError as exc:
        raise SystemExit(
            f"{changelog_path} has an invalid release date for {version}: "
            f"{match.group('date')}"
        ) from exc


def pypi_version_exists(package_name: str, version: str, timeout_seconds: float) -> bool:
    """Return whether the package version already exists on PyPI."""
    url = PYPI_VERSION_URL.format(package=package_name, version=version)
    try:
        with urllib.request.urlopen(url, timeout=timeout_seconds) as response:
            return response.status == 200
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return False
        raise SystemExit(f"PyPI check failed for {url}: HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"PyPI check failed for {url}: {exc.reason}") from exc


def pypi_final_versions(package_name: str, timeout_seconds: float) -> tuple[str, ...]:
    """Return final SemVer versions already present for the PyPI project."""
    url = PYPI_PROJECT_URL.format(package=package_name)
    try:
        with urllib.request.urlopen(url, timeout=timeout_seconds) as response:
            if response.status != 200:
                raise SystemExit(f"PyPI check failed for {url}: HTTP {response.status}")
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return ()
        raise SystemExit(f"PyPI check failed for {url}: HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"PyPI check failed for {url}: {exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f"PyPI check failed for {url}: invalid JSON") from exc

    releases = payload.get("releases")
    if not isinstance(releases, dict):
        raise SystemExit(f"PyPI check failed for {url}: missing releases object")
    final_versions = [version for version in releases if final_version_tuple(version) is not None]
    return tuple(sorted(final_versions, key=lambda item: final_version_tuple(item) or (0, 0, 0)))


def highest_pypi_final_version(package_name: str, timeout_seconds: float) -> str | None:
    """Return the highest final SemVer version already present on PyPI."""
    versions = pypi_final_versions(package_name, timeout_seconds)
    return versions[-1] if versions else None


def validate_release(args: argparse.Namespace) -> str:
    """Validate release metadata and return the accepted version."""
    tag = args.tag
    if tag is None and args.version is None:
        tag = default_release_tag()
    expected_version = expected_release_version(tag, args.version)
    metadata = project_metadata(args.pyproject)
    package_version = metadata["version"]
    if package_version != expected_version:
        raise SystemExit(
            f"expected release {expected_version!r} does not match pyproject version "
            f"{package_version!r}"
        )
    if changelog_release_date(args.changelog, expected_version) is None:
        raise SystemExit(f"{args.changelog} is missing a dated section for {expected_version}")

    package_name = args.package or metadata["name"]
    if not args.skip_pypi:
        if pypi_version_exists(
            package_name,
            expected_version,
            args.pypi_timeout,
        ):
            raise SystemExit(f"{package_name} {expected_version} already exists on PyPI")
        highest_version = highest_pypi_final_version(package_name, args.pypi_timeout)
        expected_tuple = final_version_tuple(expected_version)
        highest_tuple = final_version_tuple(highest_version) if highest_version else None
        if expected_tuple is None:
            raise SystemExit(f"release version is not final SemVer: {expected_version!r}")
        if highest_version and highest_tuple and expected_tuple <= highest_tuple:
            raise SystemExit(
                f"{package_name} {expected_version} is not newer than "
                f"the latest PyPI final release {highest_version}"
            )
    return expected_version


def main(argv: Sequence[str] | None = None) -> int:
    """Run the release preflight checks."""
    version = validate_release(parse_args(argv))
    print(f"release_preflight_ok {version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
