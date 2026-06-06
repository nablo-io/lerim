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
rationale  # noqa
retrieval_json  # noqa
brief  # noqa
records_json  # noqa
target_json  # noqa
files_json  # noqa
batch_id  # noqa
source_record_id  # noqa
target_record_id  # noqa
candidate_pairs_json  # noqa
existing_edges_json  # noqa
proposed_links_json  # noqa
candidate_profile_json  # noqa
candidate_records_json  # noqa
project_json  # noqa
recent_changes_json  # noqa
current_records_json  # noqa
replacements_json  # noqa
workspace_snapshot_json  # noqa
generation_context_json  # noqa
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
filtering_summary  # noqa
kept_durable_findings  # noqa
rejected_findings  # noqa
clustered_record_ids  # noqa
active_record_count  # noqa
cluster_count  # noqa
records_changed_since_generation  # noqa
records_missing_since_generation  # noqa
latest_run_folder  # noqa
suggested_action  # noqa
sessions_considered  # noqa
records_changed_since_previous  # noqa
recent_versions_considered  # noqa
episode_update_refs  # noqa
skip_reason  # noqa
silent_change_feedback_record  # noqa
model_size_priority_record  # noqa
provider_cost_record  # noqa
user_strategy_records  # noqa
role_split_record  # noqa
upstream_bug_report_record  # noqa
project_identity_fact  # noqa
model_setting_fact  # noqa
prompt_structure_decision  # noqa
fixture_constraint  # noqa
deferred_design_fact  # noqa
record_index  # noqa
keep  # noqa
save_any  # noqa
session_reason  # noqa
allowed_update_surfaces  # noqa
high_risk_surfaces  # noqa
diff_text  # noqa
pattern_type  # noqa
severity  # noqa
why_it_matters  # noqa
priority  # noqa
headline  # noqa
readiness_score  # noqa
recommended_actions  # noqa
questions  # noqa
metrics_json  # noqa
versions_json  # noqa
sessions_json  # noqa

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
lerim_working_memory  # noqa
lerim_context_answer  # noqa
lerim_context_search  # noqa
lerim_records_list  # noqa
lerim_trace_submit  # noqa
lerim_ingest_status  # noqa
