"""Vulture whitelist — false positives from framework/protocol methods.

Vulture cannot trace dynamic dispatch (getattr), protocol-style dispatch
(`forward`, HTTP `do_GET`), or sqlite3 attributes (`row_factory`). List them here
so CI passes cleanly.
"""

# Module protocol method — invoked by runtime dispatch
forward  # noqa

# HTTP handler methods — called by BaseHTTPRequestHandler dispatch
do_GET  # noqa
do_POST  # noqa
do_PUT  # noqa
do_PATCH  # noqa
do_DELETE  # noqa
log_message  # noqa
server_version  # noqa

# sqlite3 cursor attribute — set, not called
row_factory  # noqa

# loguru config attributes
_rotation  # noqa
_retention  # noqa
handlers  # noqa

# Pydantic model fields — populated at validation time
artifacts  # noqa

# Base class — subclassed by platform adapters
Adapter  # noqa

# Lazy import pattern
__getattr__  # noqa

# Called via getattr() dynamic dispatch in CLI (_dead_letter_action)
retry_project_jobs  # noqa
skip_project_jobs  # noqa

# Public dataclass/Pydantic fields used by serialization, validation, or callers
git_branch  # noqa
model_config  # noqa
current_utc  # noqa
candidate_profile_json  # noqa
candidate_records_json  # noqa
last_context_tokens  # noqa
metrics_version  # noqa
trace_total_lines  # noqa
current_window  # noqa
episode_updates  # noqa
durable_findings  # noqa
implementation_findings  # noqa
discarded_noise  # noqa
filtered_durable_findings  # noqa
rejected_durable_findings  # noqa
signal_filter_summary  # noqa
clustered_record_ids  # noqa
active_record_count  # noqa
cluster_count  # noqa
records_changed_since_generation  # noqa
records_missing_since_generation  # noqa
latest_run_folder  # noqa
suggested_action  # noqa
records_changed_since_previous  # noqa
episode_update_refs  # noqa
skip_reason  # noqa

# Pydantic validators registered by decorators
validate_level  # noqa

# Public configuration/provider APIs
get_config_sources  # noqa
get_project_env_path  # noqa

# Lazy module protocol and enum members used through import/attribute access
__dir__  # noqa
ACTIVE  # noqa
ARCHIVED  # noqa
CREATE  # noqa
UPDATE  # noqa
ARCHIVE  # noqa
SUPERSEDE  # noqa

# Third-party runtime attributes and public health/diagnostic helpers
graph_optimization_level  # noqa
index_health  # noqa
_prepare_search_indexes  # noqa
_upsert_embedding  # noqa
to_span_attrs  # noqa
is_server_healthy  # noqa
_mlflow_run_span  # noqa

# MCP tool functions registered by FastMCP decorators
lerim_context_brief  # noqa
lerim_context_answer  # noqa
lerim_context_search  # noqa
lerim_records_list  # noqa
lerim_trace_submit  # noqa
lerim_ingest_status  # noqa
