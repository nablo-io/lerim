test ReviewMaintainClusterSupersedesWeakerDuplicate {
  functions [ReviewMaintainCluster]
  args {
    run_instruction "Keep durable records current and non-duplicative."
    cluster_id "cluster_test"
    records_json #"
      [
        {
          "record_id": "rec_retry_weak",
          "kind": "decision",
          "title": "Keep retry state in persisted metadata",
          "body": "Store retry state in persisted metadata so work can resume. This is weakly written.",
          "status": "active",
          "created_at": "2026-05-01T00:00:00+00:00",
          "decision": "Keep retry state in persisted metadata.",
          "why": "Workers need a shared recovery point."
        },
        {
          "record_id": "rec_retry_strong",
          "kind": "decision",
          "title": "Persist retry budget in job metadata",
          "body": "Persist retry budget in job metadata so retries survive restarts and stay visible to all workers.",
          "status": "active",
          "created_at": "2026-05-02T00:00:00+00:00",
          "decision": "Persist retry budget in job metadata.",
          "why": "Retries must survive restarts and stay visible to all workers."
        }
      ]
    "#
  }
  @@assert({{ "SUPERSEDE" in (this.actions|map(attribute="action_type")|join(" ")) }})
}

test ReviewMaintainClusterKeepsRelatedDistinctRecords {
  functions [ReviewMaintainCluster]
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
          "body": "Session discovery and queue metadata live in the session catalog database.",
          "status": "active"
        }
      ]
    "#
  }
  @@assert({{ this.actions|length == 0 or "SUPERSEDE" not in (this.actions|map(attribute="action_type")|join(" ")) }})
}

test ReviewMaintainClusterSupersedesGenericDuplicate {
  functions [ReviewMaintainCluster]
  args {
    run_instruction "Prefer the most actionable durable record when two records preserve the same guidance."
    cluster_id "cluster_generic_specific"
    records_json #"
      [
        {
          "record_id": "rec_generic",
          "kind": "decision",
          "title": "Keep retry handoff restart-safe",
          "body": "Retry handoff should remain restart-safe across workers.",
          "status": "active",
          "created_at": "2026-05-03T00:00:00+00:00",
          "decision": "Keep retry handoff restart-safe.",
          "why": "Workers should recover retry state after restart."
        },
        {
          "record_id": "rec_specific",
          "kind": "decision",
          "title": "Persist retry budget in job metadata",
          "body": "Persist retry budget in job metadata so restarts and failover preserve retry state across workers.",
          "status": "active",
          "created_at": "2026-05-01T00:00:00+00:00",
          "decision": "Persist retry budget in job metadata.",
          "why": "Retries must survive restart and stay visible to all workers."
        }
      ]
    "#
  }
  @@assert({{ "SUPERSEDE" in (this.actions|map(attribute="action_type")|join(" ")) }})
}

test ReviewMaintainHealthBatchArchivesRoutineEpisode {
  functions [ReviewMaintainHealthBatch]
  args {
    run_instruction "Archive routine low-value episodes."
    batch_id "health_routine"
    records_json #"
      [
        {
          "record_id": "rec_routine_episode",
          "kind": "episode",
          "title": "Morning sync and queue check",
          "body": "Confirmed the daemon was running, retried one sync, checked the queue, and verified everything was normal.",
          "status": "active",
          "user_intent": "Check whether background sync was healthy.",
          "what_happened": "Retried one sync and confirmed the queue drained.",
          "outcomes": "No durable context beyond routine confirmation."
        }
      ]
    "#
  }
  @@assert({{ "ARCHIVE" in (this.actions|map(attribute="action_type")|join(" ")) }})
}

test ReviewMaintainHealthBatchPreservesMeaningfulEpisodeWithDurableNeighbor {
  functions [ReviewMaintainHealthBatch]
  args {
    run_instruction "Preserve meaningful incident episodes even when durable guidance exists."
    batch_id "health_meaningful_episode"
    records_json #"
      [
        {
          "record_id": "rec_neighbor_decision",
          "kind": "decision",
          "title": "Persist recovery state in the durable queue row",
          "body": "Persist recovery state in the queue row so failover can reconstruct ownership safely.",
          "status": "active",
          "decision": "Persist recovery state in the durable queue row.",
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

test ReviewMaintainHealthBatchRewritesProcessShapedDurableRecord {
  functions [ReviewMaintainHealthBatch]
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

test ReviewMaintainHealthBatchKeepsConciseDecisionWithTypedFields {
  functions [ReviewMaintainHealthBatch]
  args {
    run_instruction "Avoid churn when typed decision fields already carry reusable context."
    batch_id "health_concise_decision"
    records_json #"
      [
        {
          "record_id": "rec_concise_decision",
          "kind": "decision",
          "title": "Compared queue lease storage options",
          "body": "Compared worker-memory and queue-row storage and chose the queue row.",
          "status": "active",
          "decision": "Use queue-row storage for worker ownership.",
          "why": "Queue-row state survives restart and failover."
        }
      ]
    "#
  }
  @@assert({{ "ARCHIVE" not in (this.actions|map(attribute="action_type")|join(" ")) }})
  @@assert({{ "REVISE" not in (this.actions|map(attribute="action_type")|join(" ")) }})
  @@assert({{ "SUPERSEDE" not in (this.actions|map(attribute="action_type")|join(" ")) }})
}

test ReviewMaintainHealthBatchKeepsHealthyRecord {
  functions [ReviewMaintainHealthBatch]
  args {
    run_instruction "Avoid churn on healthy records."
    batch_id "health_noop"
    records_json #"
      [
        {
          "record_id": "rec_healthy",
          "kind": "decision",
          "title": "Persist lease ownership in the queue row",
          "body": "Authoritative lease ownership must live in the persisted queue row so recovery and failover can reconstruct ownership safely.",
          "status": "active",
          "decision": "Persist lease ownership in the queue row.",
          "why": "Recovery and failover need a persisted source of truth."
        }
      ]
    "#
  }
  @@assert({{ "ARCHIVE" not in (this.actions|map(attribute="action_type")|join(" ")) }})
  @@assert({{ "REVISE" not in (this.actions|map(attribute="action_type")|join(" ")) }})
  @@assert({{ "SUPERSEDE" not in (this.actions|map(attribute="action_type")|join(" ")) }})
}
