"""test logging."""

from __future__ import annotations

import logging
import re
from pathlib import Path

from lerim.config import logging as logging_mod


def test_configure_logging_sets_root_handler() -> None:
    logging.getLogger().handlers.clear()
    logging_mod.configure_logging("INFO")
    handlers = logging.getLogger().handlers
    assert any(isinstance(h, logging_mod._InterceptHandler) for h in handlers)


def test_configure_logging_clears_existing_handlers() -> None:
    logger = logging.getLogger("test_logger")
    dummy = logging.StreamHandler()
    logger.addHandler(dummy)
    logging_mod.configure_logging("INFO")
    assert logger.handlers == []
    assert logger.propagate is True


def test_configure_logging_keeps_anthropic_sdk_at_warning() -> None:
    logging.getLogger("anthropic").setLevel(logging.INFO)
    logging_mod.configure_logging("INFO")
    assert logging.getLogger("anthropic").level == logging.WARNING


def test_loguru_messages_do_not_use_percent_style_placeholders() -> None:
    source_root = Path(__file__).resolve().parents[2] / "src" / "lerim"
    pattern = re.compile(
        r"logger\.(?:trace|debug|info|success|warning|error|critical|exception)\(.*%[0-9\.\-]*[sdiforx]"
    )

    violations: list[str] = []
    for py_file in source_root.rglob("*.py"):
        for lineno, line in enumerate(
            py_file.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if pattern.search(line):
                relative = py_file.relative_to(source_root.parent)
                violations.append(f"{relative}:{lineno}: {line.strip()}")

    assert not violations, (
        "Found percent-style placeholders in loguru logger calls:\n"
        + "\n".join(violations)
    )


def test_log_filter_suppresses_claude_sdk_spam_by_default(monkeypatch) -> None:
    monkeypatch.delenv("LERIM_LOG_CLAUDE_SDK", raising=False)
    record = {"message": "Using bundled Claude Code CLI: /tmp/claude"}
    assert logging_mod._log_filter(record) is False


def test_log_filter_can_enable_claude_sdk_spam(monkeypatch) -> None:
    monkeypatch.setenv("LERIM_LOG_CLAUDE_SDK", "1")
    record = {"message": "Using bundled Claude Code CLI: /tmp/claude"}
    assert logging_mod._log_filter(record) is True


def test_log_filter_suppresses_anthropic_sdk_spam_by_default(monkeypatch) -> None:
    monkeypatch.delenv("LERIM_LOG_ANTHROPIC_HTTP", raising=False)
    record = {"name": "anthropic._base_client", "message": "Retrying request"}
    assert logging_mod._log_filter(record) is False


def test_log_filter_can_enable_anthropic_sdk_spam(monkeypatch) -> None:
    monkeypatch.setenv("LERIM_LOG_ANTHROPIC_HTTP", "1")
    record = {"name": "anthropic._base_client", "message": "Retrying request"}
    assert logging_mod._log_filter(record) is True
