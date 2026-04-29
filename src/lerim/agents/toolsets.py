"""Product-owned tool contracts for Lerim agents."""

from __future__ import annotations

from lerim.agents.tools import (
    archive_context,
    count_context,
    get_context,
    list_context,
    note_trace_findings,
    prune_trace_reads,
    read_trace,
    revise_context,
    save_context,
    search_context,
    supersede_context,
)
from lerim.agents.mlflow_observability import trace_mlflow_tool

READ_TRACE_TOOL_NAME = "read_trace"
SEARCH_CONTEXT_TOOL_NAME = "search_context"
GET_CONTEXT_TOOL_NAME = "get_context"
SAVE_CONTEXT_TOOL_NAME = "save_context"
REVISE_CONTEXT_TOOL_NAME = "revise_context"
NOTE_TRACE_FINDINGS_TOOL_NAME = "note_trace_findings"
PRUNE_TRACE_READS_TOOL_NAME = "prune_trace_reads"
LIST_CONTEXT_TOOL_NAME = "list_context"
ARCHIVE_CONTEXT_TOOL_NAME = "archive_context"
SUPERSEDE_CONTEXT_TOOL_NAME = "supersede_context"
COUNT_CONTEXT_TOOL_NAME = "count_context"

EXTRACT_TOOLS = (
    trace_mlflow_tool(read_trace),
    trace_mlflow_tool(search_context),
    trace_mlflow_tool(get_context),
    trace_mlflow_tool(save_context),
    trace_mlflow_tool(revise_context),
    trace_mlflow_tool(note_trace_findings),
    trace_mlflow_tool(prune_trace_reads),
)
MAINTAIN_TOOLS = (
    trace_mlflow_tool(list_context),
    trace_mlflow_tool(search_context),
    trace_mlflow_tool(get_context),
    trace_mlflow_tool(revise_context),
    trace_mlflow_tool(archive_context),
    trace_mlflow_tool(supersede_context),
)
ASK_TOOLS = (
    trace_mlflow_tool(count_context),
    trace_mlflow_tool(list_context),
    trace_mlflow_tool(search_context),
    trace_mlflow_tool(get_context),
)

EXTRACT_TOOL_NAMES = frozenset(
    {
        READ_TRACE_TOOL_NAME,
        SEARCH_CONTEXT_TOOL_NAME,
        GET_CONTEXT_TOOL_NAME,
        SAVE_CONTEXT_TOOL_NAME,
        REVISE_CONTEXT_TOOL_NAME,
        NOTE_TRACE_FINDINGS_TOOL_NAME,
        PRUNE_TRACE_READS_TOOL_NAME,
    }
)
MAINTAIN_TOOL_NAMES = frozenset(
    {
        LIST_CONTEXT_TOOL_NAME,
        SEARCH_CONTEXT_TOOL_NAME,
        GET_CONTEXT_TOOL_NAME,
        REVISE_CONTEXT_TOOL_NAME,
        ARCHIVE_CONTEXT_TOOL_NAME,
        SUPERSEDE_CONTEXT_TOOL_NAME,
    }
)
ASK_TOOL_NAMES = frozenset(
    {
        COUNT_CONTEXT_TOOL_NAME,
        LIST_CONTEXT_TOOL_NAME,
        SEARCH_CONTEXT_TOOL_NAME,
        GET_CONTEXT_TOOL_NAME,
    }
)
CURRENT_AGENT_TOOL_NAMES = EXTRACT_TOOL_NAMES | MAINTAIN_TOOL_NAMES | ASK_TOOL_NAMES
