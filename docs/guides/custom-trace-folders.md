# Custom Trace Folders

Custom trace folders are for agents or business workflows that Lerim does not
support with a built-in adapter yet.

The boundary is intentionally simple:

- Supported agents use Lerim adapters and compaction.
- Custom agents provide already-clean Lerim canonical JSONL traces.
- Lerim scans the folder as one project with type `custom`.
- Lerim does not compact, rewrite, normalize, or clean custom traces.

## User Journey

1. Export raw traces from your agent, ticket workflow, research workflow, or
   internal automation.
2. Write your own cleaner that converts those raw traces into Lerim canonical
   JSONL.
3. Put the cleaned `.jsonl` files in one folder.
4. Register that folder as a custom project.
5. Run Lerim ingest. Lerim indexes the clean files and extracts reusable
   context.

```bash
mkdir -p ~/lerim-traces/support-clean

lerim project add ~/lerim-traces/support-clean --type custom
lerim ingest --agent custom
```

Each `.jsonl` file is treated as one source session. Nested folders are fine:

```text
support-clean/
  renewals/
    run-2026-05-16-001.jsonl
  incidents/
    run-2026-05-16-002.jsonl
```

## Canonical JSONL Schema

Each non-empty line must be one JSON object with exactly these keys:

```json
{"type":"user","message":{"role":"user","content":"Customer asked for renewal approval."},"timestamp":"2026-05-16T09:00:00Z"}
{"type":"assistant","message":{"role":"assistant","content":"Agent found approval is required above EUR 500."},"timestamp":"2026-05-16T09:02:00Z"}
```

Rules:

- `type` is `user` or `assistant`
- `message.role` is `user` or `assistant`
- `message.content` is a string or a list of structured content blocks
- `timestamp` is an ISO timestamp string or `null`
- one file equals one agent/workflow session

Invalid files are skipped and logged. Lerim does not try to repair custom
traces because cleaning belongs to the source owner.

## Paste This Prompt Into Your Coding Agent

Use this prompt with Codex, Claude Code, or another coding agent in the folder
that contains your raw trace samples.

```text
You are helping me create a trace cleaner for Lerim.

Goal:
Convert raw agent or workflow traces into Lerim canonical JSONL files.

Important boundary:
Lerim custom mode expects already-clean traces. Lerim will not compact, rewrite,
normalize, redact, or repair these files. The cleaning script we write here is
the source-specific adapter and privacy boundary.

Input:
- Inspect the raw trace files in this folder.
- Identify what represents one completed agent/workflow run.
- Write one output .jsonl file per run.

Output schema:
Each non-empty JSONL line must be exactly:

{
  "type": "user" | "assistant",
  "message": {
    "role": "user" | "assistant",
    "content": string | list
  },
  "timestamp": string | null
}

Mapping guidance:
- Use "user" for the human, customer, requester, system trigger, ticket text,
  workflow request, or external input.
- Use "assistant" for the agent, automation, analyst, support bot, tool-using
  workflow, or generated response/action.
- Preserve useful chronology.
- Preserve decisions, constraints, evidence, assumptions, approvals, open
  questions, handoffs, tool results, source links, ticket ids, account ids,
  incident ids, and workflow ids when they are useful for future context.
- Drop binary blobs, screenshots, huge raw payloads, duplicate logs, progress
  noise, stack traces that add no future context, and vendor metadata that does
  not help future agents.
- Redact secrets, access tokens, private keys, passwords, session cookies,
  regulated personal data, and any fields our retention policy forbids.
- Do not invent missing facts.
- Do not use keyword matching as the main cleaning strategy. Parse the source
  structure and map its fields deliberately.

Script requirements:
- Create a Python script named clean_to_lerim_jsonl.py.
- The script should accept:
  --input <raw-trace-folder-or-file>
  --output <clean-output-folder>
- It should create the output folder if needed.
- It should validate every output line against the schema above.
- It should fail loudly on unknown source shapes instead of silently producing
  bad traces.
- It should print a summary with files read, sessions written, rows written,
  skipped items, and redaction count if redaction is implemented.

After writing the script:
1. Run it on the sample traces.
2. Show me the generated output tree.
3. Show me two short sample output lines.
4. Explain any source fields you dropped and why.
```

After the cleaner runs, register the clean output folder:

```bash
python clean_to_lerim_jsonl.py \
  --input ./raw-traces \
  --output ~/lerim-traces/support-clean

lerim project add ~/lerim-traces/support-clean --type custom
lerim ingest --agent custom
```

## How This Differs From Supported Agents

Supported sources such as Claude Code, Codex CLI, Cursor, and OpenCode use
Lerim adapters. Those adapters know where the source stores sessions, compact
source-specific events, and place canonical files under Lerim's cache.

Custom mode skips that adapter path. It reads your cleaned `.jsonl` files
directly from the registered folder and indexes them as `agent_type=custom`.

## Operational Checks

```bash
lerim project list
lerim ingest --agent custom --no-extract
lerim queue --status pending
lerim status --live
```

Use `--no-extract` when you want to verify that the folder is discovered before
running model-backed extraction.
