"""Shared timeout contracts for Lerim local server and model calls."""

from __future__ import annotations

ANSWER_REQUEST_TIMEOUT_SECONDS = 300
HTTP_API_POST_TIMEOUT_SECONDS = ANSWER_REQUEST_TIMEOUT_SECONDS + 30
