"""Product-owned tool contracts for Lerim agents."""

from __future__ import annotations

from lerim.agents.tools import (
    count_context,
    get_context,
    list_context,
    search_context,
)
from lerim.agents.mlflow_observability import trace_mlflow_tool

SEARCH_CONTEXT_TOOL_NAME = "search_context"
GET_CONTEXT_TOOL_NAME = "get_context"
LIST_CONTEXT_TOOL_NAME = "list_context"
COUNT_CONTEXT_TOOL_NAME = "count_context"

ASK_TOOLS = (
    trace_mlflow_tool(count_context),
    trace_mlflow_tool(list_context),
    trace_mlflow_tool(search_context),
    trace_mlflow_tool(get_context),
)

ASK_TOOL_NAMES = frozenset(
    {
        COUNT_CONTEXT_TOOL_NAME,
        LIST_CONTEXT_TOOL_NAME,
        SEARCH_CONTEXT_TOOL_NAME,
        GET_CONTEXT_TOOL_NAME,
    }
)
CURRENT_AGENT_TOOL_NAMES = ASK_TOOL_NAMES
