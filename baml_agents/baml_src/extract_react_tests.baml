test ScanTraceWindowCapturesDurableDecision {
  functions [ScanTraceWindow]
  args {
    run_instruction "Extract one episode and any durable project context from the trace."
    prior_episode_summary "(none yet)"
    prior_findings_summary "(none yet)"
    trace_window #"
      [8 lines, window 1-8]
      1	user: We should run extraction evals before changing extraction prompts.
      2	assistant: I will update the BAML harness and run the eval.
      3	user: Keep trace-local command logs out of long-term context.
      4	assistant: Implemented the change and ran a smoke test.
    "#
  }
  @@assert({{ this.durable_findings|length > 0 }})
}

test ScanTraceWindowSeparatesDurableAndImplementation {
  functions [ScanTraceWindow]
  args {
    run_instruction "Extract reusable context, not command history."
    prior_episode_summary "(none yet)"
    prior_findings_summary "(none yet)"
    trace_window #"
      [7 lines, window 1-7]
      1	user: The deployment checklist is the source of truth for release readiness.
      2	assistant: Ran pytest -q and fixed a local fixture path.
      3	assistant: Edited src/example.py and reran the lint command.
      4	user: The command output is just evidence, not memory.
    "#
  }
  @@assert({{ this.durable_findings|length > 0 }})
  @@assert({{ this.implementation_findings|length > 0 }})
}

test ScanTraceWindowAllowsNoDurableSignal {
  functions [ScanTraceWindow]
  args {
    run_instruction "Extract durable context only when the trace supports it."
    prior_episode_summary "(none yet)"
    prior_findings_summary "(none yet)"
    trace_window #"
      [5 lines, window 1-5]
      1	user: Please format this file.
      2	assistant: Ran the formatter.
      3	assistant: Fixed one lint complaint.
      4	assistant: Tests are green.
    "#
  }
  @@assert({{ this.durable_findings|length == 0 }})
}

test SynthesizeExtractRecordsCreatesEpisodeAndDurableRecord {
  functions [SynthesizeExtractRecords]
  args {
    run_instruction "Create exactly one episode and durable records only for reusable project context."
    episode_summary "- The session updated an extraction harness and ran a smoke eval."
    durable_findings_summary "- preference: eval-first prompt changes: Run the extraction eval before changing extraction prompts. (line 1)"
    existing_record_manifest "(none)"
  }
  @@assert({{ this.episode.user_intent|length > 0 }})
  @@assert({{ this.episode.what_happened|length > 0 }})
  @@assert({{ this.durable_records|length > 0 }})
}

test SynthesizeExtractRecordsAllowsNoDurableSignal {
  functions [SynthesizeExtractRecords]
  args {
    run_instruction "Create exactly one episode and no durable records when no reusable context exists."
    episode_summary "- The session only formatted code, fixed one lint issue, and reran tests."
    durable_findings_summary "(none)"
    existing_record_manifest "(none)"
  }
  @@assert({{ this.episode.user_intent|length > 0 }})
  @@assert({{ this.episode.what_happened|length > 0 }})
  @@assert({{ this.durable_records|length == 0 }})
}

test SynthesizeExtractRecordsDeduplicatesCoreClaim {
  functions [SynthesizeExtractRecords]
  args {
    run_instruction "Create compact durable records and merge duplicate meanings."
    episode_summary "- The session clarified a runtime state boundary after noisy implementation discussion."
    durable_findings_summary #"
      - decision: persisted source of truth: Runtime status must live in one persisted store that survives restart.
      - decision: runtime status boundary: Use the same persisted status store as the source of truth after restart.
    "#
    existing_record_manifest "(none)"
  }
  @@assert({{ this.episode.user_intent|length > 0 }})
  @@assert({{ this.durable_records|length == 1 }})
}
