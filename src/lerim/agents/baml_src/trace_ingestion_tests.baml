test ObserveSourceWindowCapturesDurableDecision {
  functions [ObserveSourceWindow]
  args {
    run_instruction "Extract one episode and any durable project context from the source session."
    prior_episode_summary "(none yet)"
    prior_findings_summary "(none yet)"
    source_window #"
      [8 lines, window 1-8]
      1	user: We should review source quality before changing extraction prompts.
      2	assistant: I will update the evaluation harness and run the review.
      3	user: Keep source-session-local tool logs out of long-term context.
      4	assistant: Implemented the change and ran a validation check.
    "#
  }
  @@assert({{ this.durable_findings|length > 0 }})
}

test ObserveSourceWindowSeparatesDurableAndImplementation {
  functions [ObserveSourceWindow]
  args {
    run_instruction "Extract reusable context, not tool-action history."
    prior_episode_summary "(none yet)"
    prior_findings_summary "(none yet)"
    source_window #"
      [7 lines, window 1-7]
      1	user: The deployment checklist is the source of truth for release readiness.
      2	assistant: Ran a validation check and fixed a local artifact path.
      3	assistant: Updated an implementation file and reran the quality check.
      4	user: The tool output is just evidence, not memory.
    "#
  }
  @@assert({{ this.durable_findings|length > 0 }})
  @@assert({{ this.implementation_findings|length > 0 }})
}

test ObserveSourceWindowAllowsNoDurableSignal {
  functions [ObserveSourceWindow]
  args {
    run_instruction "Extract durable context only when the source session supports it."
    prior_episode_summary "(none yet)"
    prior_findings_summary "(none yet)"
    source_window #"
      [5 lines, window 1-5]
      1	user: Please tidy this artifact.
      2	assistant: Ran the formatter.
      3	assistant: Fixed one style complaint.
      4	assistant: The check is green.
    "#
  }
  @@assert({{ this.durable_findings|length == 0 }})
}

test ObserveSourceWindowAvoidsIncidentalPersonalName {
  functions [ObserveSourceWindow]
  args {
    run_instruction "Extract durable context without preserving incidental personal names."
    prior_episode_summary "(none yet)"
    prior_findings_summary "(none yet)"
    source_window #"
      [5 lines, window 1-5]
      1	user: Morgan asked for extraction prompts to avoid storing names unless identity is the point.
      2	assistant: I will update the prompt generally.
      3	user: The durable rule is role-neutral memory text, not the person's name.
    "#
  }
  @@assert({{ "Morgan" not in (this.episode_update or "") }})
  @@assert({{ "Morgan" not in (this.durable_findings|map(attribute="note")|join(" ")) }})
}

test SynthesizeContextRecordsCreatesEpisodeAndDurableRecord {
  functions [SynthesizeContextRecords]
  args {
    run_instruction "Create exactly one episode and durable records only for reusable project context."
    episode_summary "- The session updated an extraction harness and ran a smoke eval."
    durable_findings_summary "- preference: eval-first prompt changes: Run the extraction eval before changing extraction prompts. (line 1)"
    existing_record_manifest "(none)"
  }
  @@assert({{ this.durable_records|length > 0 }})
}

test SynthesizeContextRecordsAllowsNoDurableSignal {
  functions [SynthesizeContextRecords]
  args {
    run_instruction "Create exactly one episode and no durable records when no reusable context exists."
    episode_summary "- The session only formatted code, fixed one lint issue, and reran tests."
    durable_findings_summary "(none)"
    existing_record_manifest "(none)"
  }
  @@assert({{ this.durable_records|length == 0 }})
}

test SynthesizeContextRecordsDeduplicatesCoreClaim {
  functions [SynthesizeContextRecords]
  args {
    run_instruction "Create compact durable records and merge duplicate meanings."
    episode_summary "- The session clarified a runtime state boundary after noisy implementation discussion."
    durable_findings_summary #"
      - decision: persisted source of truth: Runtime status must live in one persisted store that survives restart.
      - decision: runtime status boundary: Use the same persisted status store as the source of truth after restart.
    "#
    existing_record_manifest "(none)"
  }
  @@assert({{ this.durable_records|length == 1 }})
}

test SynthesizeContextRecordsAvoidsIncidentalPersonalName {
  functions [SynthesizeContextRecords]
  args {
    run_instruction "Create role-neutral episode and durable records."
    episode_summary "Morgan asked the assistant to make future extraction records avoid incidental personal names."
    durable_findings_summary "- preference: role-neutral memory text: Avoid personal names in memory text unless identity itself is the durable context."
    existing_record_manifest "(none)"
  }
  @@assert({{ "Morgan" not in (this.episode.title or "") }})
  @@assert({{ "Morgan" not in (this.episode.body or "") }})
  @@assert({{ "Morgan" not in (this.episode.user_intent or "") }})
  @@assert({{ "Morgan" not in (this.episode.what_happened or "") }})
  @@assert({{ "Morgan" not in (this.durable_records|map(attribute="body")|join(" ")) }})
}
