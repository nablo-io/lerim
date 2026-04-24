"""Central logging configuration using loguru.

Minimal stderr output for human readability. Detailed agent/LLM tracing
is handled by OpenTelemetry (see tracing.py), not by log lines.

File sinks persist logs locally for ``lerim logs`` and future tooling:
- ``~/.lerim/logs/lerim.log``   — human-readable, same format as stderr
- ``~/.lerim/logs/lerim.jsonl`` — structured JSON-per-line
"""

from __future__ import annotations

import json as _json
import logging
import os
import sys
from pathlib import Path
from typing import Any

from loguru import logger as _BASE_LOGGER
from lerim.config.settings import get_global_data_dir_path


_logger = _BASE_LOGGER

_TRUE_VALUES = {"1", "true", "yes", "on"}

LOG_DIR: Path = get_global_data_dir_path() / "logs"
"""Default directory for persistent log files."""


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
    if logger_name in ("asyncio", "httpx", "httpcore"):
        return False
    message = str(record.get("message") or "")
    if "Using bundled Claude Code CLI:" in message:
        return _env_flag("LERIM_LOG_CLAUDE_SDK", default=False)
    return True


class _JsonlSink:
    """Custom loguru sink that writes compact JSON lines to a rotating file.

    Loguru's ``format`` callable is treated as a *template* factory, not a
    final-string producer, so we use a sink object instead to get full control
    over the output bytes.
    """

    def __init__(self, path: Path, rotation: str, retention: int) -> None:
        self._path = path
        self._rotation = rotation
        self._retention = retention
        self._fh: Any = None
        self._written: int = 0
        self._max_bytes = self._parse_rotation(rotation)
        self._retention_count = retention
        self._open()

    @staticmethod
    def _parse_rotation(rotation: str) -> int:
        """Convert a rotation string like ``'10 MB'`` to bytes."""
        parts = rotation.strip().split()
        value = float(parts[0])
        unit = parts[1].upper() if len(parts) > 1 else "B"
        multipliers = {"B": 1, "KB": 1024, "MB": 1024 ** 2, "GB": 1024 ** 3}
        return int(value * multipliers.get(unit, 1))

    def _open(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self._path, "a", encoding="utf-8")
        try:
            self._written = self._path.stat().st_size
        except OSError:
            self._written = 0

    def _rotate(self) -> None:
        """Rotate files: current -> .1, .1 -> .2, etc. Drop beyond retention."""
        if self._fh:
            self._fh.close()
        # Shift existing rotated files
        for i in range(self._retention_count, 0, -1):
            src = self._path.with_suffix(f".jsonl.{i}" if i > 0 else ".jsonl")
            if i == 1:
                src = self._path
            else:
                src = self._path.parent / f"{self._path.stem}.{i - 1}{self._path.suffix}"
            dst = self._path.parent / f"{self._path.stem}.{i}{self._path.suffix}"
            if i == 1:
                src = self._path
            if src.exists():
                if i >= self._retention_count:
                    src.unlink(missing_ok=True)
                else:
                    src.rename(dst)
        self._open()

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
        if self._fh:
            self._fh.write(line)
            self._fh.flush()
            self._written += len(line.encode("utf-8"))
            if self._written >= self._max_bytes:
                self._rotate()


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
    2. **~/.lerim/logs/lerim.log** — plain-text, same layout as stderr.
    3. **~/.lerim/logs/lerim.jsonl** — structured JSON per line.

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
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    # Human-readable log (for ``tail -f``)
    _logger.add(
        str(LOG_DIR / "lerim.log"),
        level=level,
        format=_FILE_FORMAT,
        filter=_log_filter,
        rotation="10 MB",
        retention=7,
        colorize=False,
        backtrace=False,
        diagnose=False,
        encoding="utf-8",
    )

    # Structured JSONL log (for ``lerim logs`` and tooling/dashboard)
    _jsonl_sink = _JsonlSink(LOG_DIR / "lerim.jsonl", rotation="10 MB", retention=7)
    _logger.add(
        _jsonl_sink,
        level=level,
        format="{message}",
        filter=_log_filter,
        colorize=False,
        backtrace=False,
        diagnose=False,
    )

    logging.basicConfig(handlers=[_InterceptHandler()], level=0)

    for name in list(logging.root.manager.loggerDict.keys()):
        logging.getLogger(name).handlers = []
        logging.getLogger(name).propagate = True

    # Silence noisy third-party loggers
    for noisy in ("httpx", "httpcore", "openai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


configure_logging()

logger = _logger

__all__ = ["logger", "configure_logging", "LOG_DIR"]


if __name__ == "__main__":
    configure_logging(level="INFO")
    logger.info("config.logging self-test passed")
