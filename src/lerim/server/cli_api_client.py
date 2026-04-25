"""HTTP API client helpers for the Lerim server CLI."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ApiClientError(Exception):
    """Classified API client failure for clearer CLI diagnostics."""

    kind: str
    message: str
    status: int | None = None

    def __str__(self) -> str:
        """Return the human-readable failure message."""
        return self.message


def api_get(path: str, *, server_port: int) -> dict[str, Any]:
    """GET from the running Lerim server or raise a classified failure."""
    url = f"http://localhost:{server_port}{path}"
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        raise _http_api_error(path, exc) from exc
    except json.JSONDecodeError as exc:
        raise _invalid_json_api_error(path, exc) from exc
    except (urllib.error.URLError, OSError) as exc:
        raise _unreachable_api_error(exc) from exc


def api_post(
    path: str,
    body: dict[str, Any],
    *,
    server_port: int,
) -> dict[str, Any]:
    """POST JSON to the running Lerim server or raise a classified failure."""
    url = f"http://localhost:{server_port}{path}"
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode(),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=300) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        raise _http_api_error(path, exc) from exc
    except json.JSONDecodeError as exc:
        raise _invalid_json_api_error(path, exc) from exc
    except (urllib.error.URLError, OSError) as exc:
        raise _unreachable_api_error(exc) from exc


def _invalid_json_api_error(path: str, exc: json.JSONDecodeError) -> ApiClientError:
    """Build an invalid-JSON API error."""
    return ApiClientError(
        kind="invalid_json",
        message=(
            f"Lerim server returned invalid JSON for {path}: "
            f"{exc.msg} at character {exc.pos}"
        ),
    )


def _http_api_error(path: str, exc: urllib.error.HTTPError) -> ApiClientError:
    """Build an HTTP response API error, preserving a concise server detail."""
    detail = _http_error_detail(exc)
    message = f"Lerim server returned HTTP {exc.code} for {path}"
    if detail:
        message = f"{message}: {detail}"
    return ApiClientError(kind="http_error", message=message, status=exc.code)


def _http_error_detail(exc: urllib.error.HTTPError) -> str:
    """Extract a short human-readable detail from an HTTP error body."""
    try:
        raw = exc.read().decode(errors="replace").strip()
    except OSError:
        return ""
    if not raw:
        return ""
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return raw[:500]
    if isinstance(payload, dict):
        for key in ("error", "message", "detail"):
            value = payload.get(key)
            if value:
                return str(value)
        return json.dumps(payload, ensure_ascii=True)[:500]
    return str(payload)[:500]


def _unreachable_api_error(exc: urllib.error.URLError | OSError) -> ApiClientError:
    """Build an unreachable-server API error."""
    reason = getattr(exc, "reason", None) or exc
    return ApiClientError(
        kind="unreachable",
        message=f"Lerim server is not reachable: {reason}",
    )
