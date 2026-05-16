# Connecting Trace Sources

Lerim reads trace data from supported sources and turns it into reusable context.

The commands below connect the supported sources available today. For custom
agents and business workflows, use a custom trace folder instead of `connect`.

## Auto-detect

```bash
lerim connect auto
```

## Manual connect

```bash
lerim connect claude
lerim connect codex
lerim connect cursor
lerim connect opencode
```

## Custom path

```bash
lerim connect claude --path /custom/path
```

## Check connections

```bash
lerim connect list
```

## Custom agents

Custom agents do not use `lerim connect`. Export and clean their traces into
Lerim canonical JSONL, then register the clean folder as a custom project:

```bash
lerim project add ~/lerim-traces/support-clean --type custom
lerim ingest --agent custom
```

See [Custom Trace Folders](custom-trace-folders.md).
