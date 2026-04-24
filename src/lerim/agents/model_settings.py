"""Shared model settings for Lerim's agent workflows."""

from __future__ import annotations

from pydantic_ai.settings import ModelSettings


# These agents do retrieval, classification, and store maintenance. We keep the
# default sampling low-variance without using zero-temperature. MiniMax rejects
# exact zero and behaved poorly near-zero in live extraction, while fully
# stochastic provider defaults made extraction wording too unstable.
LOW_VARIANCE_AGENT_TEMPERATURE = 0.1
LOW_VARIANCE_AGENT_MODEL_SETTINGS = ModelSettings(
    temperature=LOW_VARIANCE_AGENT_TEMPERATURE,
    top_p=0.9,
)
