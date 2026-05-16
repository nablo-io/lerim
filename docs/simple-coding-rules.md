# Simple Coding Rules (Lerim)

This file defines general coding rules for Lerim.
Follow these rules unless Isaac explicitly asks to change them.

## 1) One clear path per feature

For each feature, prefer one clear execution path.
Avoid parallel/duplicate implementations for the same behavior.

## 2) Keep code minimal

Prefer fewer functions and fewer layers.
Avoid helper chains when one function can express the flow clearly.
Choose names that describe one clear action.

## 3) Use strict schemas

Use strict typed models (Pydantic/enums) for important inputs/outputs.
Do not add alias paths or preserved removed behavior unless explicitly requested.

## 4) Fail fast on missing requirements

If a package, key, or required config is missing, raise an error.
Do not add silent fallback behavior.

## 5) Config from TOML layers, API keys from env

Runtime config comes from TOML layers (default.toml → user → LERIM_CONFIG).
Only API keys use environment variables.
Keep config branching short and readable.
Do not add hidden defaults that mask misconfiguration.

## 6) Real tests over mock-only checks

Every module should have corresponding tests in `tests/`. Prefer real-path tests over mocked tests.
Tests are organized in `tests/unit/`, `tests/smoke/`, `tests/integration/`, `tests/e2e/`.
Keep existing `if __name__ == "__main__":` blocks but don't require new ones.
Validate quality of outputs, not only counts/status.

## 7) Remove old paths when replacing logic

When a new path replaces an old one, remove obsolete code.
Do not keep dead replacement code "just in case."

## 8) Keep docs and rules in ingest

When behavior changes, update the related docs and `AGENTS.md` references in the same change.
