"""Filesystem scanner for registered skill and instruction artifacts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import yaml

from lerim.skill_stewardship.schemas import ArtifactManifest, TargetFile, TargetType

MAX_FILE_BYTES = 64_000
MAX_SCAN_FILES = 80
ENTRY_NAMES = ("SKILL.md", "AGENTS.md", "CLAUDE.md", "GEMINI.md", ".cursorrules", ".windsurfrules")
KNOWN_SUPPORT_DIRS = {
    "references",
    "reference",
    "examples",
    "scripts",
    "assets",
    "agents",
    ".clinerules",
    ".cursor",
    ".roo",
}
IGNORED_SCAN_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".next",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "venv",
}


def scan_instruction_artifact(path: Path | str) -> tuple[Path, ArtifactManifest, list[TargetFile]]:
    """Read a registered instruction path and return its detected manifest."""
    root = Path(path).expanduser().resolve()
    if not root.exists():
        raise FileNotFoundError(f"instruction target not found: {root}")
    base = root if root.is_dir() else root.parent
    entry = _entry_file(root)
    files = _target_files(root=root, base=base, entry=entry)
    manifest = _manifest_for(root=root, base=base, entry=entry, files=files)
    return base, manifest, files


def artifact_name(path: Path | str, manifest: ArtifactManifest) -> str:
    """Return a stable display name for a scanned artifact."""
    root = Path(path).expanduser()
    if root.is_file():
        return root.stem
    if manifest.entry_file == "SKILL.md":
        return root.name
    return root.name or manifest.entry_file


def read_target_text(base: Path | str, relative_path: str) -> str:
    """Read one target file as UTF-8 text with replacement for malformed bytes."""
    target = _safe_child(Path(base).expanduser().resolve(), relative_path)
    return target.read_text(encoding="utf-8", errors="replace")


def _entry_file(root: Path) -> Path:
    """Resolve the entry file for a registered file or directory."""
    if root.is_file():
        return root
    for name in ENTRY_NAMES:
        candidate = root / name
        if candidate.is_file():
            return candidate
    markdown_files = sorted(root.glob("*.md"))
    if markdown_files:
        return markdown_files[0]
    text_files = sorted(root.glob("*.txt"))
    if text_files:
        return text_files[0]
    raise FileNotFoundError(f"instruction target has no supported entry file: {root}")


def _target_files(*, root: Path, base: Path, entry: Path) -> list[TargetFile]:
    """Collect bounded artifact files for review and proposal generation."""
    candidates = [entry]
    if root.is_dir():
        for child in _artifact_children(root):
            candidates.append(child)
            if len(candidates) >= MAX_SCAN_FILES:
                break
    seen: set[Path] = set()
    files: list[TargetFile] = []
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        if len(files) >= MAX_SCAN_FILES:
            break
        try:
            stat = resolved.stat()
            relative_path = resolved.relative_to(base).as_posix()
        except OSError:
            continue
        except ValueError:
            continue
        preview = None
        if _is_textish(resolved) and stat.st_size <= MAX_FILE_BYTES:
            preview = resolved.read_text(encoding="utf-8", errors="replace")
        files.append(
            TargetFile(
                relative_path=relative_path,
                file_role=_file_role(root=root, entry=entry, path=resolved),
                size_bytes=stat.st_size,
                sha256=_sha256(resolved),
                text_preview=preview,
                risk_surface=_risk_surface(root=root, path=resolved),
            )
        )
    return files


def _artifact_children(root: Path) -> list[Path]:
    """Return bounded candidate files without walking unrelated repo trees."""
    children: list[Path] = []
    for child in sorted(root.iterdir()):
        if len(children) >= MAX_SCAN_FILES:
            break
        if child.is_file() and _belongs_to_artifact(root, child):
            children.append(child)
        elif not child.is_symlink() and child.is_dir() and child.name in KNOWN_SUPPORT_DIRS:
            children.extend(_support_files(root, child, remaining=MAX_SCAN_FILES - len(children)))
    return children


def _support_files(root: Path, support_dir: Path, *, remaining: int) -> list[Path]:
    """Walk one known support directory with pruning and a hard file cap."""
    files: list[Path] = []
    stack = [support_dir]
    while stack and len(files) < remaining:
        current = stack.pop()
        try:
            entries = sorted(current.iterdir())
        except OSError:
            continue
        for entry in entries:
            if len(files) >= remaining:
                break
            if entry.is_symlink():
                continue
            if entry.is_dir():
                if entry.name not in IGNORED_SCAN_DIRS:
                    stack.append(entry)
            elif entry.is_file() and _belongs_to_artifact(root, entry):
                files.append(entry)
    return files


def _manifest_for(*, root: Path, base: Path, entry: Path, files: list[TargetFile]) -> ArtifactManifest:
    """Build a manifest for the detected instruction artifact type."""
    target_type = _target_type(root=root, entry=entry)
    entry_rel = entry.resolve().relative_to(base).as_posix()
    frontmatter = _frontmatter(entry)
    known_frontmatter = sorted(frontmatter.keys())
    supporting = [item.relative_path for item in files if item.relative_path != entry_rel]
    instruction_files = [
        item.relative_path
        for item in files
        if item.file_role in {"entry", "rule", "context", "reference"} and _is_textish(Path(item.relative_path))
    ]
    required = ["name", "description"] if target_type == "codex_skill" else []
    return ArtifactManifest(
        target_type=target_type,
        entry_file=entry_rel,
        instruction_files=instruction_files or [entry_rel],
        supporting_files=supporting,
        required_frontmatter=required,
        known_frontmatter=known_frontmatter,
        allowed_update_surfaces=_allowed_surfaces(target_type),
        high_risk_surfaces=_high_risk_surfaces(target_type),
        notes=_manifest_notes(target_type=target_type, files=files),
    )


def _target_type(*, root: Path, entry: Path) -> TargetType:
    """Classify known instruction artifact layouts from file structure."""
    if entry.name == "SKILL.md":
        if ".claude" in entry.parts or root.parent.name == "skills" and ".claude" in root.parts:
            return "claude_skill"
        if ".agents" in entry.parts or root.parent.name == "skills":
            return "codex_skill"
        return "agent_skill"
    if entry.name == "AGENTS.md":
        return "agents_md"
    if entry.name == "GEMINI.md":
        return "gemini_context"
    if entry.name == ".cursorrules" or ".cursor" in entry.parts:
        return "cursor_rules"
    if entry.name == ".windsurfrules":
        return "generic_markdown_bundle"
    if root.name == ".clinerules" or ".clinerules" in entry.parts:
        return "cline_rules"
    if entry.name == "CLAUDE.md":
        return "claude_context"
    return "generic_markdown_bundle"


def _belongs_to_artifact(root: Path, path: Path) -> bool:
    """Return whether a child file should be considered part of an artifact."""
    try:
        rel = path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    if len(rel.parts) == 1:
        return path.name in ENTRY_NAMES or path.suffix in {".md", ".txt", ".yaml", ".yml", ".json", ".toml"}
    try:
        return rel.parts[0] in KNOWN_SUPPORT_DIRS and path.stat().st_size <= MAX_FILE_BYTES
    except OSError:
        return False


def _file_role(*, root: Path, entry: Path, path: Path) -> str:
    """Classify one artifact file's role."""
    if path.resolve() == entry.resolve():
        return "entry"
    rel = path.resolve().relative_to(root.resolve()) if root.is_dir() else Path(path.name)
    if rel.parts and rel.parts[0] in {"scripts"}:
        return "script"
    if rel.parts and rel.parts[0] in {"assets"}:
        return "asset"
    if rel.parts and rel.parts[0] in {"agents", ".cursor", ".roo"}:
        return "config"
    if rel.parts and rel.parts[0] in {".clinerules"}:
        return "rule"
    if path.suffix in {".md", ".txt"}:
        return "reference"
    return "supporting"


def _risk_surface(*, root: Path, path: Path) -> str:
    """Classify update risk for a target file."""
    try:
        rel = path.resolve().relative_to(root.resolve()) if root.is_dir() else Path(path.name)
    except ValueError:
        rel = Path(path.name)
    if rel.parts and rel.parts[0] in {"scripts", "assets", "agents", ".cursor", ".roo"}:
        return "high"
    if path.name == "SKILL.md":
        return "medium"
    return "low"


def _frontmatter(path: Path) -> dict[str, Any]:
    """Parse YAML frontmatter from a markdown-ish file."""
    if not _is_textish(path):
        return {}
    text = path.read_text(encoding="utf-8", errors="replace")
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---", 4)
    if end < 0:
        return {}
    parsed = yaml.safe_load(text[4:end]) or {}
    return parsed if isinstance(parsed, dict) else {}


def _allowed_surfaces(target_type: TargetType) -> list[str]:
    """Return update surfaces the proposal pipeline may target by default."""
    if target_type in {"codex_skill", "claude_skill", "agent_skill"}:
        return ["entry_file_body", "references", "examples"]
    if target_type == "cline_rules":
        return ["markdown_rule_files"]
    if target_type in {"claude_context", "gemini_context"}:
        return ["context_body", "explicit_imports"]
    return ["markdown_body"]


def _high_risk_surfaces(target_type: TargetType) -> list[str]:
    """Return update surfaces requiring explicit review."""
    surfaces = ["scripts", "assets", "config_files", "frontmatter"]
    if target_type == "codex_skill":
        surfaces.append("agents/openai.yaml")
    if target_type in {"claude_skill", "claude_context"}:
        surfaces.extend(["allowed-tools", "disable-model-invocation", "hooks", "mcp_config"])
    return surfaces


def _manifest_notes(*, target_type: TargetType, files: list[TargetFile]) -> list[str]:
    """Summarize detected artifact constraints for the model."""
    notes = [f"Detected instruction artifact type: {target_type}."]
    if any(item.file_role == "script" for item in files):
        notes.append("Script changes are high risk and require explicit review.")
    if target_type in {"codex_skill", "claude_skill", "agent_skill"}:
        notes.append("Keep SKILL.md concise; prefer referenced files for long detail.")
    return notes


def _is_textish(path: Path) -> bool:
    """Return whether a file extension is safe to read as text."""
    return path.name in ENTRY_NAMES or path.suffix.lower() in {".md", ".txt", ".yaml", ".yml", ".json", ".toml", ".py", ".sh"}


def _sha256(path: Path) -> str:
    """Hash a file for snapshot and drift detection."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_child(base: Path, relative_path: str) -> Path:
    """Resolve a child path and reject traversal outside the target base."""
    resolved = (base / relative_path).resolve()
    if base != resolved and base not in resolved.parents:
        raise ValueError(f"path escapes instruction target: {relative_path}")
    return resolved


def manifest_json(manifest: ArtifactManifest, files: list[TargetFile]) -> str:
    """Render a compact manifest JSON string for model input."""
    payload = manifest.model_dump()
    payload["files"] = [
        item.model_dump(exclude={"text_preview"})
        for item in files
    ]
    return json.dumps(payload, indent=2, ensure_ascii=True)
