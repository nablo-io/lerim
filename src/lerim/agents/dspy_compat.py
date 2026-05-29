"""DSPy import boundary for Lerim agent pipelines."""

from __future__ import annotations

import os
from pathlib import Path

cache_root = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".lerim" / "cache")
cache_dir = cache_root / "dspy"
cache_dir.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("DSPY_CACHEDIR", str(cache_dir))
os.environ.setdefault("LITELLM_LOG", "ERROR")

import numpy  # noqa: E402,F401
import dspy  # noqa: E402

__all__ = ["dspy"]
