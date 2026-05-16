test CurateContextClusterSupersedesWeakerDuplicate {
  functions [CurateContextCluster]
  args {
    run_instruction "Keep durable records current and non-duplicative."
    cluster_id "cluster_test"
    records_json #"
      [
        {
          "record_id": "rec_recovery_weak",
          "kind": "decision",
          "title": "Keep recovery state in shared metadata",
          "body": "Store recovery state in shared metadata so interrupted work can resume. This is weakly written.",
          "status": "active",
          "created_at": "2026-05-01T00:00:00+00:00",
          "decision": "Keep recovery state in shared metadata.",
          "why": "Future runs need a shared recovery point."
        },
        {
          "record_id": "rec_recovery_strong",
          "kind": "decision",
          "title": "Persist recovery status in durable run metadata",
          "body": "Persist recovery status in durable run metadata so recovery survives restarts and stays visible to all actors.",
          "status": "active",
          "created_at": "2026-05-02T00:00:00+00:00",
          "decision": "Persist recovery status in durable run metadata.",
          "why": "Recovery must survive restarts and stay visible to all actors."
        }
      ]
    "#
  }
  @@assert({{ "SUPERSEDE" in (this.actions|map(attribute="action_type")|join(" ")) }})
}

test CurateContextClusterKeepsRelatedDistinctRecords {
  functions [CurateContextCluster]
  args {
    run_instruction "Keep related but distinct records when both help future sessions."
    cluster_id "cluster_distinct"
    records_json #"
      [
        {
          "record_id": "rec_context_db",
          "kind": "fact",
          "title": "Context records live in context.sqlite3",
          "body": "Durable context records live in the canonical context database.",
          "status": "active"
        },
        {
          "record_id": "rec_session_catalog",
          "kind": "fact",
          "title": "Session catalog lives in sessions.sqlite3",
          "body": "Session discovery metadata lives in the session catalog database.",
          "status": "active"
        }
      ]
    "#
  }
  @@assert({{ this.actions|length == 0 or "SUPERSEDE" not in (this.actions|map(attribute="action_type")|join(" ")) }})
}

test CurateContextClusterSupersedesGenericDuplicate {
  functions [CurateContextCluster]
  args {
    run_instruction "Prefer the most actionable durable record when two records preserve the same guidance."
    cluster_id "cluster_generic_specific"
    records_json #"
      [
        {
          "record_id": "rec_generic",
          "kind": "decision",
          "title": "Keep recovery handoff restart-safe",
          "body": "Recovery handoff should remain restart-safe across actors.",
          "status": "active",
          "created_at": "2026-05-03T00:00:00+00:00",
          "decision": "Keep recovery handoff restart-safe.",
          "why": "Actors should recover handoff state after restart."
        },
        {
          "record_id": "rec_specific",
          "kind": "decision",
          "title": "Persist recovery status in durable run metadata",
          "body": "Persist recovery status in durable run metadata so restarts and failover preserve recovery state across actors.",
          "status": "active",
          "created_at": "2026-05-01T00:00:00+00:00",
          "decision": "Persist recovery status in durable run metadata.",
          "why": "Recovery must survive restart and stay visible to all actors."
        }
      ]
    "#
  }
  @@assert({{ "SUPERSEDE" in (this.actions|map(attribute="action_type")|join(" ")) }})
}

test CurateRecordHealthBatchArchivesRoutineEpisode {
  functions [CurateRecordHealthBatch]
  args {
    run_instruction "Archive routine low-value episodes."
    batch_id "health_routine"
    records_json #"
      [
        {
          "record_id": "rec_routine_episode",
          "kind": "episode",
          "title": "Morning background health check",
          "body": "Confirmed the background process was running, reran one ingestion, checked pending work, and verified everything was normal.",
          "status": "active",
          "user_intent": "Check whether background processing was healthy.",
          "what_happened": "Reran one ingestion and confirmed pending work drained.",
          "outcomes": "No durable context beyond routine confirmation."
        }
      ]
    "#
  }
  @@assert({{ "ARCHIVE" in (this.actions|map(attribute="action_type")|join(" ")) }})
}

test CurateRecordHealthBatchPreservesMeaningfulEpisodeWithDurableNeighbor {
  functions [CurateRecordHealthBatch]
  args {
    run_instruction "Preserve meaningful incident episodes even when durable guidance exists."
    batch_id "health_meaningful_episode"
    records_json #"
      [
        {
          "record_id": "rec_neighbor_decision",
          "kind": "decision",
          "title": "Persist recovery state in the durable coordination record",
          "body": "Persist recovery state in the durable coordination record so failover can reconstruct ownership safely.",
          "status": "active",
          "decision": "Persist recovery state in the durable coordination record.",
          "why": "Restart and failover need one authoritative recovery source."
        },
        {
          "record_id": "rec_meaningful_episode",
          "kind": "episode",
          "title": "Restart recovery incident",
          "body": "Resolved a real restart recovery incident and clarified why persisted recovery state matters during failover.",
          "status": "active",
          "user_intent": "Understand why restart recovery was failing.",
          "what_happened": "Traced the incident through restart, failover, and ownership recovery behavior.",
          "outcomes": "Clarified the recovery story around persisted ownership state."
        }
      ]
    "#
  }
  @@assert({{ "rec_meaningful_episode" not in ((this.actions|selectattr("action_type", "equalto", "ARCHIVE")|map(attribute="record_id")|join(" "))) }})
}

test CurateRecordHealthBatchRewritesProcessShapedDurableRecord {
  functions [CurateRecordHealthBatch]
  args {
    run_instruction "Rewrite durable records that narrate process instead of reusable context."
    batch_id "health_process_durable"
    records_json #"
      [
        {
          "record_id": "rec_process_decision",
          "kind": "decision",
          "title": "Task audit for provider normalization migration",
          "body": "Reviewed the provider normalization migration and concluded that provider events should be normalized at the adapter boundary.",
          "status": "active",
          "decision": "Normalize provider events at the adapter boundary.",
          "why": "Downstream code should depend on one stable contract."
        }
      ]
    "#
  }
  @@assert({{ "REVISE" in (this.actions|map(attribute="action_type")|join(" ")) }})
}

test CurateRecordHealthBatchKeepsConciseDecisionWithTypedFields {
  functions [CurateRecordHealthBatch]
  args {
    run_instruction "Avoid churn when typed decision fields already carry reusable context."
    batch_id "health_concise_decision"
    records_json #"
      [
        {
          "record_id": "rec_concise_decision",
          "kind": "decision",
          "title": "Compared ownership storage options",
          "body": "Compared process-local and durable coordination storage and chose the durable coordination record.",
          "status": "active",
          "decision": "Use durable coordination storage for ownership state.",
          "why": "Durable coordination state survives restart and failover."
        }
      ]
    "#
  }
  @@assert({{ "ARCHIVE" not in (this.actions|map(attribute="action_type")|join(" ")) }})
  @@assert({{ "REVISE" not in (this.actions|map(attribute="action_type")|join(" ")) }})
  @@assert({{ "SUPERSEDE" not in (this.actions|map(attribute="action_type")|join(" ")) }})
}

test CurateRecordHealthBatchKeepsHealthyRecord {
  functions [CurateRecordHealthBatch]
  args {
    run_instruction "Avoid churn on healthy records."
    batch_id "health_noop"
    records_json #"
      [
        {
          "record_id": "rec_healthy",
          "kind": "decision",
          "title": "Persist ownership in the durable coordination record",
          "body": "Authoritative ownership must live in the durable coordination record so recovery and failover can reconstruct ownership safely.",
          "status": "active",
          "decision": "Persist ownership in the durable coordination record.",
          "why": "Recovery and failover need a persisted source of truth."
        }
      ]
    "#
  }
  @@assert({{ "ARCHIVE" not in (this.actions|map(attribute="action_type")|join(" ")) }})
  @@assert({{ "REVISE" not in (this.actions|map(attribute="action_type")|join(" ")) }})
  @@assert({{ "SUPERSEDE" not in (this.actions|map(attribute="action_type")|join(" ")) }})
}
