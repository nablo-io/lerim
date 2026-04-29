"""Central logging configuration using loguru.

Minimal stderr output for human readability. Detailed agent/LLM tracing
is handled by OpenTelemetry (see tracing.py), not by log lines.

File sinks persist logs locally for ``lerim logs`` and future tooling:
- ``~/.lerim/logs/YYYY/MM/DD/lerim.log``   — human-readable process log
- ``~/.lerim/logs/YYYY/MM/DD/lerim.jsonl`` — structured JSON-per-line log
"""

from __future__ import annotations

import json as _json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger as _BASE_LOGGER
from lerim.config.settings import get_global_data_dir_path


_logger = _BASE_LOGGER

_TRUE_VALUES = {"1", "true", "yes", "on"}

LOG_ROOT: Path = get_global_data_dir_path() / "logs"
"""Root directory for persistent log files."""

LOG_DIR: Path = LOG_ROOT
"""Root directory for persistent log files. Kept for callers/tests."""


def dated_log_dir(moment: datetime | None = None, *, root: Path | None = None) -> Path:
    """Return the UTC day directory for persistent logs."""
    when = moment or datetime.now(timezone.utc)
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    when = when.astimezone(timezone.utc)
    base = root or LOG_DIR
    return base / f"{when.year:04d}" / f"{when.month:02d}" / f"{when.day:02d}"


def log_file_path(filename: str, moment: datetime | None = None) -> Path:
    """Return the dated path for one log filename."""
    return dated_log_dir(moment) / filename


def iter_log_files(filename: str = "lerim.jsonl") -> list[Path]:
    """Return dated log files in chronological path order."""
    return sorted(LOG_DIR.glob(f"[0-9][0-9][0-9][0-9]/[0-9][0-9]/[0-9][0-9]/{filename}"))


def _env_flag(name: str, default: bool) -> bool:
    """Return boolean environment flag with common truthy values."""
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in _TRUE_VALUES


_FORMAT = "<green>{time:HH:mm:ss}</green> | <level>{level:<8}</level> | <level>{message}</level>\n"

_FILE_FORMAT = "{time:HH:mm:ss} | {level:<8} | {message}\n"
"""Plain-text format for the human-readable log file (no ANSI colours)."""


def _log_filter(record: dict) -> bool:
    """Hide noisy third-party SDK logs unless explicitly opted in."""
    logger_name = str(record.get("name") or "")
    if logger_name.startswith("openai"):
        return _env_flag("LERIM_LOG_OPENAI_HTTP", default=False)
    if logger_name.startswith("anthropic"):
        return _env_flag("LERIM_LOG_ANTHROPIC_HTTP", default=False)
    if logger_name in ("asyncio", "httpx", "httpcore"):
        return False
    message = str(record.get("message") or "")
    if "Using bundled Claude Code CLI:" in message:
        return _env_flag("LERIM_LOG_CLAUDE_SDK", default=False)
    return True


class _DailyTextSink:
    """Loguru sink that writes formatted text to the current day file."""

    def __init__(self, filename: str) -> None:
        self._filename = filename

    def write(self, message: str) -> None:
        """Write one already-formatted log line to the record's day file."""
        record = message.record  # type: ignore[attr-defined]
        path = log_file_path(self._filename, record["time"])
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(str(message))


class _JsonlSink:
    """Loguru sink that writes compact JSON lines to the current day file.

    Loguru's ``format`` callable is treated as a *template* factory, not a
    final-string producer, so we use a sink object instead to get full control
    over the output bytes.
    """

    def __init__(self, filename: str = "lerim.jsonl") -> None:
        self._filename = filename

    def write(self, message: str) -> None:
        """Called by loguru for each log message. ``message`` is the formatted string."""
        record = message.record  # type: ignore[attr-defined]
        entry = {
            "ts": record["time"].isoformat(),
            "level": record["level"].name,
            "module": record["module"],
            "message": record["message"],
            "extra": {k: v for k, v in (record.get("extra") or {}).items()},
        }
        line = _json.dumps(entry, ensure_ascii=True, default=str) + "\n"
        path = log_file_path(self._filename, record["time"])
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line)


class _InterceptHandler(logging.Handler):
    """Forward stdlib logging records to loguru."""

    def emit(self, record: logging.LogRecord) -> None:
        """Forward one stdlib log record into loguru."""
        try:
            level = _logger.level(record.levelname).name
        except ValueError:
            level = record.levelno
        try:
            msg = record.getMessage()
        except (TypeError, ValueError):
            # Some SDK log messages contain stray % chars that break
            # getMessage()'s %-formatting.  Fall back to raw message.
            msg = str(record.msg)
        _logger.opt(exception=record.exc_info).log(level, msg)


def configure_logging(level: str | None = None) -> None:
    """Configure loguru and capture stdlib logging.

    Three sinks are registered:
    1. **stderr** — coloured, human-readable (existing behaviour).
    2. **~/.lerim/logs/YYYY/MM/DD/lerim.log** — plain text.
    3. **~/.lerim/logs/YYYY/MM/DD/lerim.jsonl** — structured JSON per line.

    File sinks use the same ``_log_filter`` and log level as stderr.
    """
    global _logger
    level = level or os.getenv("LERIM_LOG_LEVEL", "INFO")
    colorize = _env_flag("LERIM_LOG_COLOR", default=sys.stderr.isatty())

    _BASE_LOGGER.remove()
    _logger = _BASE_LOGGER

    # ── stderr sink (existing) ────────────────────────────────────────
    _logger.add(
        sys.stderr,
        level=level,
        format=_FORMAT,
        filter=_log_filter,
        colorize=colorize,
        backtrace=False,
        diagnose=False,
    )

    # ── persistent file sinks ─────────────────────────────────────────
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)

        # Human-readable log (for ``tail -f``)
        _logger.add(
            _DailyTextSink("lerim.log"),
            level=level,
            format=_FILE_FORMAT,
            filter=_log_filter,
            colorize=False,
            backtrace=False,
            diagnose=False,
        )

        # Structured JSONL log (for ``lerim logs`` and tooling/dashboard)
        _jsonl_sink = _JsonlSink("lerim.jsonl")
        _logger.add(
            _jsonl_sink,
            level=level,
            format="{message}",
            filter=_log_filter,
            colorize=False,
            backtrace=False,
            diagnose=False,
        )
    except OSError as exc:
        _logger.warning(f"persistent Lerim logs disabled: {exc}")

    logging.basicConfig(handlers=[_InterceptHandler()], level=0)

    for name in list(logging.root.manager.loggerDict.keys()):
        logging.getLogger(name).handlers = []
        logging.getLogger(name).propagate = True

    # Silence noisy third-party loggers
    for noisy in ("httpx", "httpcore", "openai", "anthropic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


logger = _logger

__all__ = [
    "logger",
    "configure_logging",
    "dated_log_dir",
    "iter_log_files",
    "log_file_path",
    "LOG_DIR",
    "LOG_ROOT",
]


if __name__ == "__main__":
    configure_logging(level="INFO")
    logger.info("config.logging self-test passed")
