"""DSPy trace-ingestion pipeline over source-session windows."""

from __future__ import annotations

import json
from contextlib import nullcontext
from typing import Any, Callable

from lerim.agents.dspy_compat import dspy

from lerim.agents.context_curator import run_context_curator
from lerim.agents.model_helpers import call_model_step, prediction_payload
from lerim.agents.model_runtime import ModelRuntime, build_model_runtime
from lerim.agents.trace_ingestion.coding_records import (
    apply_coding_retention_decisions,
    coding_eval_polish_to_synthesized,
)
from lerim.agents.trace_ingestion.persistence import (
    PersistenceContext,
    load_session_durable_record_ids,
    persist_synthesized_extraction,
)
from lerim.agents.trace_ingestion.signatures import (
    AnnotateOperationalRecordRoles,
    ExtractCodingProjectIdentitySlot,
    ExtractCodingStrategySlots,
    FilterDurableSignal,
    GuardSynthesizedContextRecords,
    ObserveSourceWindow,
    PolishCodingEvalContextRecords,
    PolishContextRecords,
    SelectCodingDurableRecords,
    SynthesizeContextRecords,
)
from lerim.agents.trace_ingestion.source_text import (
    visible_source_lines,
    visible_user_source_lines,
)
from lerim.agents.trace_ingestion.summaries import (
    _durable_findings_summary,
    _episode_summary,
    _filtered_durable_findings_summary,
    _findings_summary,
    _implementation_summary,
    _rejected_durable_findings_summary,
    _synthesis_episode_summary,
    _window_line_refs,
)
from lerim.agents.trace_ingestion.windowing import (
    compute_request_budget,
    read_trace_window,
    trace_line_count,
    window_char_budget,
)
from lerim.config.settings import Config
from lerim.profiles import format_signal_pack_context, normalize_signal_pack_id


class TraceIngestionPipeline(dspy.Module):
    """Scan source windows, filter durable signal, synthesize records, and persist."""

    def __init__(
        self,
        *,
        persistence_context: PersistenceContext,
        config: Config,
        run_instruction: str,
        existing_record_manifest: str,
        provider: str | None = None,
        model_name: str | None = None,
        api_base_url: str | None = None,
        api_key: str | None = None,
        temperature: float | None = None,
        max_model_steps: int | None = None,
        progress: bool = False,
        runtime: ModelRuntime | None = None,
        steps: dict[str, Any] | None = None,
        reconcile_records: Callable[[list[str]], object] | None = None,
    ) -> None:
        """Create the trace-ingestion pipeline.

        reconcile_records, when provided, is called with the durable record IDs this
        trace wrote and replaces the default write-time reconciliation pass (the real
        scoped context curator). Tests inject a fake to assert the trigger without a
        model; production leaves it None.
        """
        super().__init__()
        self.persistence_context = persistence_context
        self.config = config
        self.run_instruction = run_instruction
        self.existing_record_manifest = existing_record_manifest
        self.provider = provider
        self.model_name = model_name
        self.api_base_url = api_base_url
        self.api_key = api_key
        self.temperature = temperature
        self.max_model_steps = max(
            1,
            int(max_model_steps or compute_request_budget(persistence_context.trace_path)),
        )
        self.progress = progress
        self.runtime = runtime
        self._reconcile_records = reconcile_records
        self.adapter = dspy.JSONAdapter()
        self.source_profile_id = normalize_signal_pack_id(
            persistence_context.source_profile
        )
        self.source_profile_context = format_signal_pack_context(self.source_profile_id)
        configured_steps = steps or {}
        self.observe_step = configured_steps.get("observe") or dspy.Predict(
            ObserveSourceWindow
        )
        self.filter_step = configured_steps.get("filter") or dspy.Predict(
            FilterDurableSignal
        )
        self.synthesize_step = configured_steps.get("synthesize") or dspy.Predict(
            SynthesizeContextRecords
        )
        self.guard_step = configured_steps.get("guard") or dspy.Predict(
            GuardSynthesizedContextRecords
        )
        self.strategy_step = configured_steps.get("strategy") or dspy.Predict(
            ExtractCodingStrategySlots
        )
        self.polish_step = configured_steps.get("polish") or dspy.Predict(
            PolishContextRecords
        )
        self.project_identity_step = configured_steps.get("project_identity") or dspy.Predict(
            ExtractCodingProjectIdentitySlot
        )
        self.retention_step = configured_steps.get("retention") or dspy.Predict(
            SelectCodingDurableRecords
        )
        self.coding_polish_step = configured_steps.get("coding_polish") or dspy.Predict(
            PolishCodingEvalContextRecords
        )
        self.role_annotation_step = configured_steps.get("role_annotation") or dspy.Predict(
            AnnotateOperationalRecordRoles
        )
        self.uses_real_model = any(
            configured_steps.get(name) is None
            for name in (
                "observe",
                "filter",
                "synthesize",
                "guard",
                "strategy",
                "polish",
                "project_identity",
                "retention",
                "coding_polish",
                "role_annotation",
            )
        )

    def forward(self) -> dict[str, Any]:
        """Run the full ingestion workflow."""
        total_lines = trace_line_count(self.persistence_context.trace_path)
        state: dict[str, Any] = {
            "observations": [],
            "llm_calls": 0,
            "next_line": 1,
            "trace_total_lines": total_lines,
            "done": False,
            "completion_summary": "",
            "episode_updates": [],
            "episode_update_refs": [],
            "durable_findings": [],
            "implementation_findings": [],
            "discarded_noise": [],
        }
        self.resolve_scope(state)
        while int(state.get("next_line") or 1) <= total_lines:
            self.read_window(state)
            self.scan_window(state)
        self.filter_signals(state)
        self.synthesize_records(state)
        self.guard_records(state)
        self.polish_records(state)
        self.annotate_record_roles(state)
        self.persist_records(state)
        return state

    def model_context(self):
        """Return a DSPy context only when real predictors need a configured LM."""
        if not self.uses_real_model:
            return nullcontext()
        if self.runtime is None:
            self.runtime = build_model_runtime(
                config=self.config,
                provider=self.provider,
                model_name=self.model_name,
                api_base_url=self.api_base_url,
                api_key=self.api_key,
                temperature=self.temperature,
            )
        return dspy.context(lm=self.runtime.lm, adapter=self.adapter)

    def resolve_scope(self, state: dict[str, Any]) -> None:
        """Record the source/scope boundary as an explicit phase."""
        scope = self.persistence_context.scope_identity
        state["observations"].append(
            observation(
                "resolve_scope",
                True,
                f"scope={scope.scope_type}:{scope.scope_id}",
                {
                    "scope_type": scope.scope_type,
                    "scope_id": scope.scope_id,
                    "scope_label": scope.label,
                    "source_name": self.persistence_context.source_name,
                    "source_profile": self.source_profile_id,
                },
            )
        )

    def read_window(self, state: dict[str, Any]) -> None:
        """Read the next budgeted trace window into transient state."""
        char_budget = window_char_budget(
            state=state,
            run_instruction=self.run_instruction,
            existing_record_manifest=self.existing_record_manifest,
            episode_summary=_episode_summary(state),
            durable_findings_summary=_durable_findings_summary(state),
            implementation_summary=_implementation_summary(state),
        )
        window = read_trace_window(
            trace_path=self.persistence_context.trace_path,
            start_line=int(state.get("next_line") or 1),
            total_lines=int(state.get("trace_total_lines") or 0),
            char_budget=char_budget,
        )
        state["current_window"] = window
        state["next_line"] = int(window["end_line"]) + 1
        if self.progress:
            print(
                f"  trace-ingestion window {window['start_line']}-{window['end_line']} "
                f"chars={len(window['text'])}",
                flush=True,
            )
        state["observations"].append(
            observation(
                "read_window",
                True,
                window["header"],
                {
                    "start_line": window["start_line"],
                    "end_line": window["end_line"],
                    "char_budget": char_budget,
                },
            )
        )

    def scan_window(self, state: dict[str, Any]) -> None:
        """Scan the current source window into rolling findings."""
        self.require_budget(state)
        window = state.get("current_window") or {}
        if not window.get("text"):
            return
        if self.progress:
            print(
                f"  trace-ingestion observe {int(state['llm_calls']) + 1}/{self.max_model_steps}",
                flush=True,
            )
        with self.model_context():
            result, retry_observations, attempts = call_model_step(
                lambda: self.observe_step(
                    run_instruction=self.run_instruction,
                    source_profile_context=self.source_profile_context,
                    prior_episode_summary=_episode_summary(state),
                    prior_findings_summary=_findings_summary(state),
                    source_window=str(window["text"]),
                ),
                stage="scan_window",
                progress=self.progress,
                progress_label="trace-ingestion",
            )
        payload = prediction_payload(result, output_field="scan")
        episode_update = str(payload.get("episode_update") or "").strip()
        durable = [
            prediction_payload(item)
            for item in payload.get("durable_findings") or []
        ]
        implementation = [
            prediction_payload(item)
            for item in payload.get("implementation_findings") or []
        ]
        noise = [
            str(item).strip()
            for item in payload.get("discarded_noise") or []
            if str(item).strip()
        ]
        state["llm_calls"] += attempts
        if episode_update:
            state["episode_updates"].append(episode_update)
            state["episode_update_refs"].extend(_window_line_refs(window))
        state["durable_findings"].extend(durable)
        state["implementation_findings"].extend(implementation)
        state["discarded_noise"].extend(noise)
        state["observations"].extend(
            [
                *retry_observations,
                observation(
                    "scan_window",
                    True,
                    (
                        f"window={window.get('start_line')}-{window.get('end_line')} "
                        f"durable={len(durable)} implementation={len(implementation)}"
                    ),
                    {
                        "start_line": window.get("start_line"),
                        "end_line": window.get("end_line"),
                    },
                ),
            ]
        )

    def filter_signals(self, state: dict[str, Any]) -> None:
        """Run the final durable-signal filter before synthesis."""
        self.require_budget(state)
        if self.progress:
            print(
                f"  trace-ingestion filter {int(state['llm_calls']) + 1}/{self.max_model_steps}",
                flush=True,
            )
        with self.model_context():
            result, retry_observations, attempts = call_model_step(
                lambda: self.filter_step(
                    run_instruction=self.run_instruction,
                    source_profile_context=self.source_profile_context,
                    episode_summary=_episode_summary(state),
                    durable_findings_summary=_durable_findings_summary(state),
                    implementation_summary=_implementation_summary(state),
                    existing_record_manifest=self.existing_record_manifest or "(none)",
                ),
                stage="filter_signals",
                progress=self.progress,
                progress_label="trace-ingestion",
            )
        payload = prediction_payload(result, output_field="result")
        kept = [
            prediction_payload(item)
            for item in payload.get("kept_durable_findings") or []
        ]
        rejected = [
            prediction_payload(item)
            for item in payload.get("rejected_findings") or []
        ]
        summary = str(payload.get("filtering_summary") or "").strip()
        state["llm_calls"] += attempts
        state["filtered_durable_findings"] = kept
        state["rejected_durable_findings"] = rejected
        state["signal_filter_summary"] = summary
        state["observations"].extend(
            [
                *retry_observations,
                observation(
                    "filter_signals",
                    True,
                    f"kept={len(kept)} rejected={len(rejected)}",
                    {"filtering_summary": summary},
                ),
            ]
        )

    def synthesize_records(self, state: dict[str, Any]) -> None:
        """Synthesize final episode and durable record candidates."""
        self.require_budget(state)
        if self.progress:
            print(
                f"  trace-ingestion synthesize {int(state['llm_calls']) + 1}/{self.max_model_steps}",
                flush=True,
            )
        with self.model_context():
            result, retry_observations, attempts = call_model_step(
                lambda: self.synthesize_step(
                    run_instruction=self.run_instruction,
                    source_profile_context=self.source_profile_context,
                    episode_summary=_synthesis_episode_summary(state),
                    durable_findings_summary=_filtered_durable_findings_summary(state),
                    existing_record_manifest=self.existing_record_manifest or "(none)",
                ),
                stage="synthesize_records",
                progress=self.progress,
                progress_label="trace-ingestion",
            )
        payload = prediction_payload(result, output_field="records")
        state["llm_calls"] += attempts
        state["synthesized"] = payload
        durable_count = len(payload.get("durable_records") or [])
        state["observations"].extend(
            [
                *retry_observations,
                observation(
                    "synthesize_records",
                    True,
                    f"durable_records={durable_count}",
                    {},
                ),
            ]
        )

    def guard_records(self, state: dict[str, Any]) -> None:
        """Run a focused final guard over synthesized records before persistence."""
        llm_calls = int(state.get("llm_calls") or 0)
        if self.source_profile_id == "coding" and llm_calls + 2 >= self.max_model_steps:
            state["observations"].append(
                observation(
                    "guard_records",
                    True,
                    "skipped_for_coding_profile_budget",
                    {},
                )
            )
            return
        self.require_budget(state)
        if self.progress:
            print(
                f"  trace-ingestion guard {int(state['llm_calls']) + 1}/{self.max_model_steps}",
                flush=True,
            )
        with self.model_context():
            result, retry_observations, attempts = call_model_step(
                lambda: self.guard_step(
                    run_instruction=self.run_instruction,
                    source_profile_context=self.source_profile_context,
                    episode_summary=_synthesis_episode_summary(state),
                    durable_findings_summary=_filtered_durable_findings_summary(state),
                    implementation_summary=_implementation_summary(state),
                    existing_record_manifest=self.existing_record_manifest or "(none)",
                    rejected_findings_summary=_rejected_durable_findings_summary(state),
                    draft_records_json=json.dumps(state.get("synthesized"), ensure_ascii=True),
                ),
                stage="guard_records",
                progress=self.progress,
                progress_label="trace-ingestion",
            )
        payload = prediction_payload(result, output_field="records")
        state["llm_calls"] += attempts
        state["synthesized"] = payload
        durable_count = len(payload.get("durable_records") or [])
        state["observations"].extend(
            [
                *retry_observations,
                observation(
                    "guard_records",
                    True,
                    f"durable_records={durable_count}",
                    {},
                ),
            ]
        )

    def polish_records(self, state: dict[str, Any]) -> None:
        """Run source-profile-specific last-mile record polish."""
        if self.source_profile_id == "coding":
            self.polish_coding_records(state)
            return
        self.require_budget(state)
        if self.progress:
            print(
                f"  trace-ingestion polish {int(state['llm_calls']) + 1}/{self.max_model_steps}",
                flush=True,
            )
        with self.model_context():
            result, retry_observations, attempts = call_model_step(
                lambda: self.polish_step(
                    run_instruction=self.run_instruction,
                    source_profile_context=self.source_profile_context,
                    episode_summary=_synthesis_episode_summary(state),
                    durable_findings_summary=_filtered_durable_findings_summary(state),
                    implementation_summary=_implementation_summary(state),
                    rejected_findings_summary=_rejected_durable_findings_summary(state),
                    draft_records_json=json.dumps(state.get("synthesized"), ensure_ascii=True),
                ),
                stage="polish_records",
                progress=self.progress,
                progress_label="trace-ingestion",
            )
        payload = prediction_payload(result, output_field="records")
        state["llm_calls"] += attempts
        state["synthesized"] = payload
        state["observations"].extend(
            [
                *retry_observations,
                observation(
                    "polish_records",
                    True,
                    f"durable_records={len(payload.get('durable_records') or [])}",
                    {},
                ),
            ]
        )

    def polish_coding_records(self, state: dict[str, Any]) -> None:
        """Run coding-profile polish, slot repair, and retention review."""
        source_lines = visible_source_lines(self.persistence_context.trace_path)
        user_source_lines = visible_user_source_lines(self.persistence_context.trace_path)
        observations: list[dict[str, Any]] = []
        strategy_slots, retry_observations = self.extract_coding_strategy_slots(
            state,
            user_source_lines,
        )
        observations.extend(retry_observations)
        coding_payload, retry_observations = self.run_coding_polish(
            state,
            source_lines,
        )
        observations.extend(retry_observations)
        project_identity_slots, retry_observations = self.extract_project_identity_slots(
            state,
            coding_payload,
            source_lines,
        )
        observations.extend(retry_observations)
        payload = coding_eval_polish_to_synthesized(
            coding_payload,
            trace_path=self.persistence_context.trace_path,
            supplemental_fixed_slots=project_identity_slots,
            supplemental_strategy_slots=strategy_slots,
            supplemental_findings=[
                *(state.get("durable_findings") or []),
                *(state.get("filtered_durable_findings") or []),
                *(state.get("rejected_durable_findings") or []),
            ],
        )
        payload, retry_observations = self.apply_coding_retention(
            state,
            payload,
            source_lines,
        )
        observations.extend(retry_observations)
        state["synthesized"] = payload
        state["observations"].extend(
            [
                *observations,
                observation(
                    "polish_records",
                    True,
                    f"durable_records={len(payload.get('durable_records') or [])}",
                    {},
                ),
            ]
        )

    def extract_coding_strategy_slots(
        self,
        state: dict[str, Any],
        user_source_lines: str,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Extract user-authored strategy slots when visible user source exists."""
        if user_source_lines == "(none)":
            return {}, []
        self.require_budget(state)
        if self.progress:
            print(
                f"  trace-ingestion user_strategy {int(state['llm_calls']) + 1}/{self.max_model_steps}",
                flush=True,
            )
        with self.model_context():
            result, observations, attempts = call_model_step(
                lambda: self.strategy_step(
                    run_instruction=self.run_instruction,
                    source_profile_context=self.source_profile_context,
                    user_source_lines=user_source_lines,
                    durable_findings_summary=_filtered_durable_findings_summary(state),
                    rejected_findings_summary=_rejected_durable_findings_summary(state),
                ),
                stage="extract_coding_user_strategy",
                progress=self.progress,
                progress_label="trace-ingestion",
            )
        state["llm_calls"] += attempts
        return prediction_payload(result, output_field="slots"), observations

    def run_coding_polish(
        self,
        state: dict[str, Any],
        visible_source_lines: str,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Run the coding-profile polish model step."""
        self.require_budget(state)
        if self.progress:
            print(
                f"  trace-ingestion polish {int(state['llm_calls']) + 1}/{self.max_model_steps}",
                flush=True,
            )
        with self.model_context():
            result, observations, attempts = call_model_step(
                lambda: self.coding_polish_step(
                    run_instruction=self.run_instruction,
                    source_profile_context=self.source_profile_context,
                    episode_summary=_synthesis_episode_summary(state),
                    durable_findings_summary=_filtered_durable_findings_summary(state),
                    implementation_summary=_implementation_summary(state),
                    rejected_findings_summary=_rejected_durable_findings_summary(state),
                    draft_records_json=json.dumps(state.get("synthesized"), ensure_ascii=True),
                    visible_source_lines=visible_source_lines,
                ),
                stage="polish_records",
                progress=self.progress,
                progress_label="trace-ingestion",
            )
        state["llm_calls"] += attempts
        return prediction_payload(result, output_field="records"), observations

    def extract_project_identity_slots(
        self,
        state: dict[str, Any],
        coding_payload: dict[str, Any],
        visible_source_lines: str,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Repair missing project identity using visible source when budget remains."""
        if (
            coding_payload.get("project_identity_fact")
            or visible_source_lines == "(none)"
            or int(state["llm_calls"]) >= self.max_model_steps
        ):
            return {}, []
        if self.progress:
            print(
                f"  trace-ingestion project_identity {int(state['llm_calls']) + 1}/{self.max_model_steps}",
                flush=True,
            )
        with self.model_context():
            result, observations, attempts = call_model_step(
                lambda: self.project_identity_step(
                    run_instruction=self.run_instruction,
                    source_profile_context=self.source_profile_context,
                    visible_source_lines=visible_source_lines,
                ),
                stage="extract_coding_project_identity",
                progress=self.progress,
                progress_label="trace-ingestion",
            )
        state["llm_calls"] += attempts
        return prediction_payload(result, output_field="slots"), observations

    def apply_coding_retention(
        self,
        state: dict[str, Any],
        payload: dict[str, Any],
        visible_source_lines: str,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Run the final coding retention critic when there is budget and signal."""
        if payload.get("durable_records") and int(state["llm_calls"]) < self.max_model_steps:
            if self.progress:
                print(
                    f"  trace-ingestion coding_retention {int(state['llm_calls']) + 1}/{self.max_model_steps}",
                    flush=True,
                )
            with self.model_context():
                retention_result, retention_observations, attempts = call_model_step(
                    lambda: self.retention_step(
                        run_instruction=self.run_instruction,
                        source_profile_context=self.source_profile_context,
                        visible_source_lines=visible_source_lines,
                        final_records_json=json.dumps(payload, ensure_ascii=True),
                    ),
                    stage="select_coding_durable_records",
                    progress=self.progress,
                    progress_label="trace-ingestion",
                )
            state["llm_calls"] += attempts
            return apply_coding_retention_decisions(
                payload,
                prediction_payload(retention_result, output_field="retention"),
            ), retention_observations
        return payload, []

    def annotate_record_roles(self, state: dict[str, Any]) -> None:
        """Annotate accepted durable records with operational roles."""
        payload = dict(state.get("synthesized") or {})
        records = list(payload.get("durable_records") or [])
        if not records:
            return
        if int(state.get("llm_calls") or 0) >= self.max_model_steps:
            role_counts: dict[str, int] = {}
            for record in records:
                role = str(record.get("record_role") or "general")
                role_counts[role] = role_counts.get(role, 0) + 1
            state["observations"].append(
                observation(
                    "annotate_record_roles",
                    True,
                    f"skipped_for_budget {role_count_summary(role_counts)}",
                    {"role_counts": role_counts, "skipped_for_budget": True},
                )
            )
            return
        if self.progress:
            print(
                f"  trace-ingestion role_annotation {int(state['llm_calls']) + 1}/{self.max_model_steps}",
                flush=True,
            )
        with self.model_context():
            result, observations, attempts = call_model_step(
                lambda: self.role_annotation_step(
                    run_instruction=self.run_instruction,
                    source_profile_context=self.source_profile_context,
                    durable_findings_summary=_filtered_durable_findings_summary(state),
                    implementation_summary=_implementation_summary(state),
                    rejected_findings_summary=_rejected_durable_findings_summary(state),
                    durable_records_json=json.dumps(records, ensure_ascii=True),
                ),
                stage="annotate_record_roles",
                progress=self.progress,
                progress_label="trace-ingestion",
            )
        state["llm_calls"] += attempts
        annotations = role_annotations_by_index(
            prediction_payload(result, output_field="roles"),
            record_count=len(records),
        )
        annotated_records: list[dict[str, Any]] = []
        role_counts: dict[str, int] = {}
        for index, record in enumerate(records):
            annotation = annotations.get(index) or {}
            role = str(annotation.get("record_role") or record.get("record_role") or "general")
            role_counts[role] = role_counts.get(role, 0) + 1
            annotated_records.append(
                {
                    **record,
                    "record_role": role,
                    "role_payload": annotation.get("role_payload") or record.get("role_payload"),
                }
            )
        state["synthesized"] = {**payload, "durable_records": annotated_records}
        state["observations"].extend(
            [
                *observations,
                observation(
                    "annotate_record_roles",
                    True,
                    role_count_summary(role_counts),
                    {"role_counts": role_counts},
                ),
            ]
        )

    def persist_records(self, state: dict[str, Any]) -> None:
        """Persist synthesized records and finish the workflow."""
        observations, done, completion_summary = persist_synthesized_extraction(
            state.get("synthesized"),
            self.persistence_context,
        )
        if self.progress:
            print(f"  trace-ingestion persist done={done}", flush=True)
        state["observations"].extend(observations)
        state["done"] = done
        state["completion_summary"] = completion_summary
        self._reconcile_new_records(state)

    def _reconcile_new_records(self, state: dict[str, Any]) -> None:
        """Reconcile newly written durable records against existing neighbors.

        Runs a scoped context-curation pass over the durable records this trace just
        wrote plus their active semantic neighbors, so an update supersedes the
        record it replaces at write time instead of waiting for the periodic curator.
        The scoped pass protects the just-written seeds, so a brand-new record is
        never retired here; residual conflicts fall through to the periodic curator.

        No-ops for scope-only ingestion (the curator needs a project identity), when
        the persist step did not complete, and when no durable record was written.
        Reconciliation is best-effort: a failure is recorded in the event stream but
        never fails an already-completed ingest.
        """
        if not state.get("done"):
            return
        ctx = self.persistence_context
        if ctx.project_identity is None:
            return
        new_record_ids = load_session_durable_record_ids(ctx)
        if not new_record_ids:
            return
        try:
            if self._reconcile_records is not None:
                self._reconcile_records(new_record_ids)
            elif self.uses_real_model:
                run_context_curator(
                    context_db_path=ctx.context_db_path,
                    project_identity=ctx.project_identity,
                    session_id=f"{ctx.session_id}:reconcile",
                    config=self.config,
                    provider=self.provider,
                    model_name=self.model_name,
                    api_base_url=self.api_base_url,
                    api_key=self.api_key,
                    temperature=self.temperature,
                    seed_record_ids=new_record_ids,
                    progress=self.progress,
                )
            else:
                return
        except Exception as exc:  # best-effort: a completed ingest must not fail here
            state["observations"].append(
                observation(
                    "reconcile_on_write",
                    False,
                    f"{type(exc).__name__}: {exc}",
                    {"seed_record_count": len(new_record_ids)},
                )
            )
            return
        state["observations"].append(
            observation(
                "reconcile_on_write",
                True,
                f"seeds={len(new_record_ids)}",
                {"seed_record_count": len(new_record_ids)},
            )
        )

    def require_budget(self, state: dict[str, Any]) -> None:
        """Raise before starting a model step when the request budget is exhausted."""
        if int(state.get("llm_calls") or 0) >= self.max_model_steps:
            raise RuntimeError(
                f"trace ingestion exceeded max_model_steps={self.max_model_steps}"
            )


def observation(
    action: str,
    ok: bool,
    content: str,
    args: dict[str, Any],
) -> dict[str, Any]:
    """Build one trace-ingestion event payload."""
    return {
        "action": action,
        "ok": ok,
        "content": content,
        "args": args,
        "done": False,
        "completion_summary": "",
    }


def role_annotations_by_index(
    payload: dict[str, Any],
    *,
    record_count: int,
) -> dict[int, dict[str, Any]]:
    """Return validated role annotations keyed by durable-record index."""
    annotations: dict[int, dict[str, Any]] = {}
    for value in payload.get("annotations") or []:
        annotation = prediction_payload(value)
        index = annotation.get("record_index")
        if not isinstance(index, int) or index < 0 or index >= record_count:
            continue
        role = str(annotation.get("record_role") or "").strip()
        if not role:
            continue
        annotations[index] = {
            "record_role": role,
            "role_payload": annotation.get("role_payload"),
        }
    return annotations


def role_count_summary(role_counts: dict[str, int]) -> str:
    """Return a compact role-count summary for ingestion observations."""
    if not role_counts:
        return "roles=none"
    parts = [
        f"{role}={count}"
        for role, count in sorted(role_counts.items(), key=lambda item: item[0])
    ]
    return "roles " + " ".join(parts)
