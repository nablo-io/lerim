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

# Used by 30+ test cases in test_transcript.py
format_transcript  # noqa

# Called via getattr() dynamic dispatch in CLI (_dead_letter_action)
retry_project_jobs  # noqa
skip_project_jobs  # noqa

# Public dataclass/Pydantic fields used by serialization, validation, or callers
git_branch  # noqa
model_config  # noqa
last_context_tokens  # noqa
metrics_version  # noqa
trace_total_lines  # noqa
current_window  # noqa
episode_updates  # noqa
durable_findings  # noqa
implementation_findings  # noqa
discarded_noise  # noqa
records_changed_since_generation  # noqa
latest_run_folder  # noqa
suggested_action  # noqa
records_changed_since_previous  # noqa
skip_reason  # noqa

# Pydantic validators and Agent validators registered by decorators
validate_level  # noqa
_require_session_episode  # noqa

# Tool wrappers preserve runtime signatures for PydanticAI schema generation
__signature__  # noqa

# Public tool registry and public configuration/provider APIs
CURRENT_AGENT_TOOL_NAMES  # noqa
build_pydantic_model_from_provider  # noqa
get_config_sources  # noqa

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
to_span_attrs  # noqa
is_server_healthy  # noqa
_mlflow_run_span  # noqa
