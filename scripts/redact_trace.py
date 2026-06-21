#!/usr/bin/env python3
"""Defense-in-depth secret/PII redaction for Lerim trace files.

Reads a JSONL, JSON, or plain-text trace, applies conservative regex-based
redaction for common secret and PII patterns, and writes a cleaned copy. This is
**defense-in-depth**, not a compliance boundary. It catches obvious high-risk
tokens before import; it does not replace a customer-owned cleaning, redaction,
and retention process for regulated data.

Lerim extraction is selective, but it is not a privacy firewall. Run this (or your
own cleaner) before a trace enters a custom folder or `lerim trace import`.

Usage:
    python scripts/redact_trace.py input.jsonl -o cleaned.jsonl
    python scripts/redact_trace.py input.jsonl -o cleaned.jsonl --json

No network calls. Reads one file, writes one file.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Iterable

# Each pattern: (name, compiled regex, replacement token).
# Patterns are deliberately conservative — they target high-signal secret shapes
# and common PII. False negatives are expected; this is a first-pass net, not DLP.
REDACTION_PATTERNS: list[tuple[str, re.Pattern[str], str]] = [
    # API keys / tokens with common prefixes
    ("api_key_prefixed", re.compile(r"\b(sk-|pk-|rk-|tok_|key_|api_)[A-Za-z0-9_\-]{16,}"), "[REDACTED_API_KEY]"),
    ("bearer_token", re.compile(r"\b(Bearer\s+)[A-Za-z0-9_\-\.=]{20,}", re.IGNORECASE), r"\1[REDACTED_TOKEN]"),
    ("aws_access_key", re.compile(r"\b(AKIA|ASIA)[A-Z0-9]{16}"), "[REDACTED_AWS_KEY]"),
    ("aws_secret", re.compile(r"\b(aws_secret_access_key|aws_session_token)\s*[=:]\s*[A-Za-z0-9/+=]{30,}", re.IGNORECASE), "[REDACTED_AWS_SECRET]"),
    ("gcp_service_account", re.compile(r'"type"\s*:\s*"service_account"'), '"type":"[REDACTED_SERVICE_ACCOUNT]"'),
    ("stripe_key", re.compile(r"\b(sk|pk|rk)_(live|test)_[A-Za-z0-9]{20,}"), "[REDACTED_STRIPE_KEY]"),
    ("github_token", re.compile(r"\b(gh[opsu]_|github_pat_)[A-Za-z0-9_]{20,}"), "[REDACTED_GITHUB_TOKEN]"),
    ("slack_token", re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}"), "[REDACTED_SLACK_TOKEN]"),
    ("private_key_block", re.compile(r"-----BEGIN (RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"), "[REDACTED_PRIVATE_KEY_BLOCK]"),
    # Generic password/secret assignment
    ("password_assign", re.compile(r"\b(password|passwd|pwd|secret|api_key|apikey|access_token|auth_token)\b\s*[=:]\s*\S+", re.IGNORECASE), "[REDACTED_SECRET]"),
    # PII
    ("email", re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"), "[REDACTED_EMAIL]"),
    # Credit card (basic 13-16 digit groups)
    ("credit_card", re.compile(r"\b(?:\d[ -]*?){13,16}\b"), "[REDACTED_CARD]"),
    # SSN-ish (US)
    ("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[REDACTED_SSN]"),
    # IBAN (basic)
    ("iban", re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b"), "[REDACTED_IBAN]"),
]


def redact_text(text: str, counts: dict[str, int]) -> str:
    """Apply all redaction patterns to a single string, mutating counts."""
    for name, pattern, replacement in REDACTION_PATTERNS:
        new_text, n = pattern.subn(replacement, text)
        if n:
            counts[name] = counts.get(name, 0) + n
            text = new_text
    return text


def iter_records(path: Path) -> Iterable[tuple[int, str]]:
    """Yield (line_number, raw_line) for JSONL; for JSON/text yield the whole file."""
    suffix = path.suffix.lower()
    if suffix == ".jsonl":
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            yield i, line
    else:
        yield 1, path.read_text(encoding="utf-8")


def redact_jsonl_line(line: str, counts: dict[str, int]) -> str:
    """Redact content fields inside one JSONL record, preserving structure."""
    stripped = line.strip()
    if not stripped:
        return line
    try:
        obj = json.loads(stripped)
    except json.JSONDecodeError:
        # Not JSON — treat the raw line as text.
        return redact_text(line, counts)
    _redact_obj(obj, counts)
    return json.dumps(obj, ensure_ascii=False)


def _redact_obj(obj: object, counts: dict[str, int]) -> None:
    """Recursively redact string values inside a parsed JSON object in place."""
    if isinstance(obj, dict):
        for key, value in list(obj.items()):
            if isinstance(value, str):
                obj[key] = redact_text(value, counts)
            else:
                _redact_obj(value, counts)
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            if isinstance(item, str):
                obj[i] = redact_text(item, counts)
            else:
                _redact_obj(item, counts)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Defense-in-depth secret/PII redaction for Lerim trace files.",
    )
    parser.add_argument("input", type=Path, help="Input trace file (.jsonl, .json, or .txt).")
    parser.add_argument("-o", "--output", type=Path, required=True, help="Output cleaned trace file.")
    parser.add_argument(
        "--text",
        action="store_true",
        help="Treat the input as plain text even if .jsonl (redact raw lines, not JSON fields).",
    )
    args = parser.parse_args(argv)

    if not args.input.exists():
        print(f"error: input file not found: {args.input}", file=sys.stderr)
        return 2

    counts: dict[str, int] = {}
    out_lines: list[str] = []
    is_jsonl = args.input.suffix.lower() == ".jsonl" and not args.text

    for _line_no, raw in iter_records(args.input):
        if is_jsonl:
            out_lines.append(redact_jsonl_line(raw, counts))
        else:
            out_lines.append(redact_text(raw, counts))

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(out_lines) + "\n", encoding="utf-8")

    total = sum(counts.values())
    print(f"Redacted {total} item(s) -> {args.output}")
    if counts:
        width = max(len(name) for name in counts)
        for name, n in sorted(counts.items()):
            print(f"  {name:<{width}}  {n}")
    else:
        print("  (no patterns matched; this does NOT mean the trace is clean)")
    print(
        "\nThis is defense-in-depth, not a compliance boundary. Review the output "
        "before import,\nespecially for regulated or customer data.",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
