"""HTTP route handlers for skill stewardship endpoints."""

from __future__ import annotations

from http import HTTPStatus
from typing import Any, Callable
from urllib.parse import unquote

from lerim.config.logging import logger
from lerim.server.skill_api import (
    api_skill_proposal_apply,
    api_skill_proposal_reject,
    api_skill_proposal_show,
    api_skill_proposal_update,
    api_skill_proposals,
    api_skill_refresh,
    api_skill_runs,
    api_skill_target_add,
    api_skill_target_mode,
    api_skill_target_show,
    api_skill_targets,
)


def handle_skill_get(handler: Any, path: str, query: dict[str, list[str]]) -> bool:
    """Serve skill stewardship GET routes; return whether the route matched."""
    if path == "/api/skills/targets":
        handler._json(api_skill_targets())
        return True
    if path == "/api/skills/proposals":
        handler._json(
            api_skill_proposals(
                target_id=_query_param(query, "target_id") or None,
                status=_query_param(query, "status") or None,
            )
        )
        return True
    if path == "/api/skills/runs":
        handler._json(api_skill_runs(limit=_parse_int(_query_param(query, "limit", "20"), 20, minimum=1, maximum=200)))
        return True
    if path.startswith("/api/skills/targets/"):
        target_id = unquote(path.split("/api/skills/targets/", 1)[1])
        if not target_id:
            handler._error(HTTPStatus.BAD_REQUEST, "Missing target id")
            return True
        try:
            handler._json(api_skill_target_show(target_id))
        except KeyError as exc:
            handler._error(HTTPStatus.NOT_FOUND, str(exc))
        return True
    if path.startswith("/api/skills/proposals/"):
        proposal_id = unquote(path.split("/api/skills/proposals/", 1)[1])
        if not proposal_id:
            handler._error(HTTPStatus.BAD_REQUEST, "Missing proposal id")
            return True
        try:
            handler._json(api_skill_proposal_show(proposal_id))
        except KeyError as exc:
            handler._error(HTTPStatus.NOT_FOUND, str(exc))
        return True
    return False


def handle_skill_post(handler: Any, path: str, read_body: Callable[[], dict[str, Any] | None]) -> bool:
    """Serve skill stewardship POST routes; return whether the route matched."""
    if path == "/api/skills/targets":
        body = read_body()
        if body is None:
            return True
        target_path = str(body.get("path") or "").strip()
        if not target_path:
            handler._error(HTTPStatus.BAD_REQUEST, "Missing 'path'")
            return True
        requested_mode = body.get("update_mode") if "update_mode" in body else None
        try:
            handler._json(
                api_skill_target_add(
                    path=target_path,
                    name=str(body.get("name") or "").strip() or None,
                    description=str(body.get("description") or "").strip() or None,
                    update_mode=str(requested_mode).strip() if requested_mode is not None else None,
                )
            )
        except (FileNotFoundError, ValueError) as exc:
            handler._error(HTTPStatus.BAD_REQUEST, str(exc))
        return True
    if path.startswith("/api/skills/targets/") and path.endswith("/refresh"):
        target_id = unquote(path.split("/api/skills/targets/", 1)[1].rsplit("/refresh", 1)[0])
        body = read_body()
        if body is None:
            return True
        try:
            handler._json(
                api_skill_refresh(
                    target_id,
                    record_limit=_parse_int(str(body.get("record_limit") or "80"), 80, minimum=1, maximum=500),
                )
            )
        except KeyError as exc:
            handler._error(HTTPStatus.NOT_FOUND, str(exc))
        except Exception as exc:
            logger.exception("skill refresh failed")
            handler._error(HTTPStatus.INTERNAL_SERVER_ERROR, f"Skill refresh failed: {exc}")
        return True
    if path.startswith("/api/skills/targets/") and path.endswith("/mode"):
        target_id = unquote(path.split("/api/skills/targets/", 1)[1].rsplit("/mode", 1)[0])
        body = read_body()
        if body is None:
            return True
        try:
            handler._json(
                api_skill_target_mode(
                    target_id_or_name=target_id,
                    update_mode=str(body.get("update_mode") or "review"),
                    auto_apply_policy=body.get("auto_apply_policy") if isinstance(body.get("auto_apply_policy"), dict) else None,
                )
            )
        except (KeyError, ValueError) as exc:
            handler._error(HTTPStatus.BAD_REQUEST, str(exc))
        return True
    if path.startswith("/api/skills/proposals/") and path.endswith("/apply"):
        proposal_id = unquote(path.split("/api/skills/proposals/", 1)[1].rsplit("/apply", 1)[0])
        try:
            handler._json(api_skill_proposal_apply(proposal_id))
        except (KeyError, ValueError) as exc:
            handler._error(HTTPStatus.BAD_REQUEST, str(exc))
        return True
    if path.startswith("/api/skills/proposals/") and path.endswith("/reject"):
        proposal_id = unquote(path.split("/api/skills/proposals/", 1)[1].rsplit("/reject", 1)[0])
        try:
            handler._json(api_skill_proposal_reject(proposal_id))
        except KeyError as exc:
            handler._error(HTTPStatus.NOT_FOUND, str(exc))
        except ValueError as exc:
            handler._error(HTTPStatus.BAD_REQUEST, str(exc))
        return True
    if path.startswith("/api/skills/proposals/") and path.endswith("/edit"):
        proposal_id = unquote(path.split("/api/skills/proposals/", 1)[1].rsplit("/edit", 1)[0])
        body = read_body()
        if body is None:
            return True
        patch_json = body.get("patch_json")
        if not isinstance(patch_json, dict):
            handler._error(HTTPStatus.BAD_REQUEST, "Missing 'patch_json'")
            return True
        try:
            handler._json(api_skill_proposal_update(proposal_id, patch_json))
        except (KeyError, ValueError) as exc:
            handler._error(HTTPStatus.BAD_REQUEST, str(exc))
        return True
    return False


def _query_param(query: dict[str, list[str]], key: str, default: str = "") -> str:
    """Extract a single query parameter value."""
    return (query.get(key) or [default])[0]


def _parse_int(raw: str | None, default: int, *, minimum: int = 0, maximum: int = 10_000) -> int:
    """Parse integer query/body parameter and clamp to bounds."""
    try:
        value = int(raw or default)
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))
