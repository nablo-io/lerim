"""CLI command handlers and parser setup for `lerim skill`."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from lerim.server.skill_api import (
    api_skill_proposal_apply,
    api_skill_proposal_reject,
    api_skill_proposal_show,
    api_skill_proposals,
    api_skill_refresh,
    api_skill_target_add,
    api_skill_target_mode,
    api_skill_target_show,
    api_skill_targets,
)

_SKILL_TARGETS: dict[str, Path] = {
    "agents": Path.home() / ".agents" / "skills" / "lerim",
    "claude": Path.home() / ".claude" / "skills" / "lerim",
}
"""Skill install targets: ~/.agents/skills (shared by most agents) + ~/.claude/skills (Claude-specific)."""


def _emit(message: object = "", *, file: Any | None = None) -> None:
    """Write one CLI output line to stdout or a provided file-like target."""
    target = file if file is not None else sys.stdout
    target.write(f"{message}\n")


def _cmd_skill(args: argparse.Namespace) -> int:
    """Dispatch skill install, target, proposal, and refresh commands."""
    action = getattr(args, "skill_action", None)
    if action == "install":
        return _cmd_skill_install()
    if action == "target":
        return _cmd_skill_target(args)
    if action == "proposal":
        return _cmd_skill_proposal(args)
    if action == "refresh":
        return _cmd_skill_refresh(args)
    _emit("Usage: lerim skill install|target|proposal|refresh")
    return 2


def _cmd_skill_install() -> int:
    """Install Lerim skill files into coding agent directories."""
    from lerim.skills import SKILLS_DIR

    skill_files = [SKILLS_DIR / "SKILL.md", SKILLS_DIR / "cli-reference.md"]
    missing = [f for f in skill_files if not f.exists()]
    if missing:
        _emit(f"Skill files not found in package: {missing}", file=sys.stderr)
        return 1

    installed = []
    for label, dest in _SKILL_TARGETS.items():
        dest.mkdir(parents=True, exist_ok=True)
        for src in skill_files:
            (dest / src.name).write_text(src.read_text())
        installed.append(f"~/.{label}/skills/lerim" if label != "agents" else "~/.agents/skills/lerim")

    _emit(f"Installed lerim skill to: {', '.join(installed)}")
    _emit("  ~/.agents/skills/lerim  -> Cursor, Codex, OpenCode, and others")
    _emit("  ~/.claude/skills/lerim  -> Claude Code")
    return 0


def _cmd_skill_target(args: argparse.Namespace) -> int:
    """Manage registered instruction targets."""
    subaction = getattr(args, "target_action", None)
    try:
        if subaction == "add":
            result = api_skill_target_add(
                path=args.path,
                name=getattr(args, "name", None),
                description=getattr(args, "description", None),
                update_mode=getattr(args, "mode", None),
            )
            target = result["target"]
            _emit(f"Registered {target['name']} ({target['target_type']})")
            _emit(f"- id: {target['target_id']}")
            _emit(f"- path: {target['path']}")
            _emit(f"- entry: {target['entry_file']}")
            return 0
        if subaction == "list":
            result = api_skill_targets()
            if getattr(args, "json", False):
                _emit(json.dumps(result, indent=2, ensure_ascii=True))
                return 0
            for target in result["targets"]:
                _emit(
                    f"{target['target_id']}  {target['name']}  "
                    f"{target['target_type']}  {target['update_mode']}  {target['path']}"
                )
            return 0
        if subaction == "show":
            result = api_skill_target_show(args.target)
            _emit(json.dumps(result, indent=2, ensure_ascii=True))
            return 0
        if subaction == "auto-apply":
            policy = {"enabled": bool(args.enable), "max_risk": args.risk}
            mode = "auto_apply" if args.enable else "review"
            result = api_skill_target_mode(
                target_id_or_name=args.target,
                update_mode=mode,
                auto_apply_policy=policy,
            )
            target = result["target"]
            _emit(f"Updated {target['name']} mode to {target['update_mode']}")
            return 0
    except (FileNotFoundError, KeyError, ValueError) as exc:
        _emit(str(exc), file=sys.stderr)
        return 1
    _emit("Usage: lerim skill target add|list|show|auto-apply")
    return 2


def _cmd_skill_proposal(args: argparse.Namespace) -> int:
    """Review and apply skill update proposals."""
    subaction = getattr(args, "proposal_action", None)
    try:
        if subaction == "list":
            result = api_skill_proposals(
                target_id=getattr(args, "target_id", None),
                status=getattr(args, "status", None),
            )
            if getattr(args, "json", False):
                _emit(json.dumps(result, indent=2, ensure_ascii=True))
                return 0
            for proposal in result["proposals"]:
                _emit(f"{proposal['proposal_id']}  {proposal['status']}  {proposal['risk_level']}  {proposal['title']}")
            return 0
        if subaction == "show":
            result = api_skill_proposal_show(args.proposal)
            _emit(json.dumps(result, indent=2, ensure_ascii=True))
            return 0
        if subaction == "apply":
            result = api_skill_proposal_apply(args.proposal)
            _emit(f"Applied {result['proposal']['proposal_id']}")
            return 0
        if subaction == "reject":
            result = api_skill_proposal_reject(args.proposal)
            _emit(f"Rejected {result['proposal']['proposal_id']}")
            return 0
    except (KeyError, ValueError) as exc:
        _emit(str(exc), file=sys.stderr)
        return 1
    _emit("Usage: lerim skill proposal list|show|apply|reject")
    return 2


def _cmd_skill_refresh(args: argparse.Namespace) -> int:
    """Run LLM-backed proposal generation for a registered target."""
    try:
        result = api_skill_refresh(args.target, record_limit=args.record_limit)
    except (KeyError, ValueError) as exc:
        _emit(str(exc), file=sys.stderr)
        return 1
    if getattr(args, "json", False):
        _emit(json.dumps(result, indent=2, ensure_ascii=True))
        return 0
    _emit(f"Refresh run {result['run_id']}")
    _emit(f"- records scanned: {result['records_scanned']}")
    _emit(f"- signals: {result['signals_created']}")
    _emit(f"- proposals: {len(result['proposals'])}")
    _emit(f"- applied: {result['applied']}")
    return 0


def add_skill_parser(sub: Any, formatter_class: type[argparse.HelpFormatter]) -> None:
    """Register the `lerim skill` parser tree."""
    skill = sub.add_parser(
        "skill",
        formatter_class=formatter_class,
        help="Install Lerim skill files and manage watched skill updates",
        description="Install Lerim skill files and manage registered instruction artifacts.",
    )
    skill_sub = skill.add_subparsers(dest="skill_action")
    skill_sub.add_parser(
        "install",
        formatter_class=formatter_class,
        help="Copy skill files into agent directories",
    )
    skill_target = skill_sub.add_parser(
        "target",
        formatter_class=formatter_class,
        help="Register, inspect, and configure watched instruction artifacts",
    )
    target_sub = skill_target.add_subparsers(dest="target_action")
    target_add = target_sub.add_parser("add", formatter_class=formatter_class, help="Register a skill or instruction path")
    target_add.add_argument("path", help="Skill directory, SKILL.md, AGENTS.md, or related instruction file.")
    target_add.add_argument("--name", help="Display name for this watched target.")
    target_add.add_argument("--description", help="What Lerim should improve in this target.")
    target_add.add_argument(
        "--mode",
        choices=["review", "auto_apply", "paused"],
        default=None,
        help="Update mode. New targets default to review; existing targets keep their current mode unless provided.",
    )
    target_list = target_sub.add_parser("list", formatter_class=formatter_class, help="List registered targets")
    target_list.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="Emit machine-readable JSON.")
    target_show = target_sub.add_parser("show", formatter_class=formatter_class, help="Show one registered target")
    target_show.add_argument("target", help="Target id or name.")
    target_auto = target_sub.add_parser(
        "auto-apply",
        formatter_class=formatter_class,
        help="Enable or disable low-risk automatic proposal application",
    )
    target_auto.add_argument("target", help="Target id or name.")
    auto_group = target_auto.add_mutually_exclusive_group(required=True)
    auto_group.add_argument("--enable", action="store_true", help="Enable auto-apply.")
    auto_group.add_argument("--disable", dest="enable", action="store_false", help="Disable auto-apply.")
    target_auto.add_argument("--risk", choices=["low", "medium", "high"], default="low")

    skill_refresh = skill_sub.add_parser(
        "refresh",
        formatter_class=formatter_class,
        help="Run LLM-backed proposal generation for a target",
    )
    skill_refresh.add_argument("target", help="Target id or name.")
    skill_refresh.add_argument("--record-limit", type=int, default=80)
    skill_refresh.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="Emit machine-readable JSON.")

    skill_proposal = skill_sub.add_parser(
        "proposal",
        formatter_class=formatter_class,
        help="Review and apply skill update proposals",
    )
    proposal_sub = skill_proposal.add_subparsers(dest="proposal_action")
    proposal_list = proposal_sub.add_parser("list", formatter_class=formatter_class, help="List proposals")
    proposal_list.add_argument("--target-id", help="Filter by target id.")
    proposal_list.add_argument("--status", help="Filter by proposal status.")
    proposal_list.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="Emit machine-readable JSON.")
    proposal_show = proposal_sub.add_parser("show", formatter_class=formatter_class, help="Show one proposal")
    proposal_show.add_argument("proposal", help="Proposal id.")
    proposal_apply = proposal_sub.add_parser("apply", formatter_class=formatter_class, help="Apply one proposal")
    proposal_apply.add_argument("proposal", help="Proposal id.")
    proposal_reject = proposal_sub.add_parser("reject", formatter_class=formatter_class, help="Reject one proposal")
    proposal_reject.add_argument("proposal", help="Proposal id.")
    skill.set_defaults(func=_cmd_skill)
