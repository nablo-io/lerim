# lerim project

Register or remove project paths.

## Summary

Projects are path registrations.
They are not storage roots.

## Examples

```bash
lerim project add .
lerim project add ~/codes/my-app
lerim project add ~/lerim-traces/support-clean --type custom
lerim project list
lerim project remove my-app
```

## Source type

`lerim project add` defaults to `--type supported`.

Use `--type supported` for normal projects whose sessions come from connected
Claude Code, Codex CLI, Cursor, or OpenCode adapters.

Use `--type custom` for folders of already-clean Lerim canonical JSONL traces.
Custom projects are read directly. Lerim does not compact, rewrite, normalize,
or clean files in custom folders.

## How it works

Lerim stores the project path in user config.
Durable context still lives in the global database.
