# lerim connect

Manage agent platform connections for session ingestion.

## Overview

Register, list, or remove agent platform connections. Lerim reads session data from connected platforms to build shared context records.

Supported platforms: `claude`, `codex`, `cursor`, `opencode`

## Syntax

```bash
lerim connect list
lerim connect
lerim connect auto
lerim connect <platform> [--path PATH]
lerim connect remove <platform>
```

## Subcommands

### Auto-detect all platforms

```bash
lerim connect auto
```

Scans default paths for each platform and registers any that are found.

### Connect a specific platform

```bash
lerim connect claude
lerim connect codex
lerim connect cursor
lerim connect opencode
```

### Connect with custom path

```bash
lerim connect claude --path /custom/path/to/claude/sessions
lerim connect cursor --path ~/my-cursor-data/globalStorage
```

The path is expanded (`~` is resolved) and must exist on disk.

### List connections

```bash
lerim connect list
lerim connect
```

### Disconnect a platform

```bash
lerim connect remove claude
```

## Parameters

<div class="param-field">
  <div class="param-header">
    <span class="param-name">platform_name</span>
    <span class="param-type">string</span>
    <span class="param-badge default">optional</span>
  </div>
  <p class="param-desc">Action or platform: <code>list</code>, <code>auto</code>, <code>remove</code>, or a platform name. Omit it to list connected platforms.</p>
</div>

<div class="param-field">
  <div class="param-header">
    <span class="param-name">--path</span>
    <span class="param-type">string</span>
  </div>
  <p class="param-desc">Custom filesystem path to the platform's session store. Overrides auto-detected default.</p>
</div>

## Default session paths

| Platform | Default path |
|----------|-------------|
| `claude` | `~/.claude/projects/` |
| `codex` | `~/.codex/sessions/` |
| `cursor` | `~/Library/Application Support/Cursor/User/globalStorage/` (macOS) |
| `opencode` | `~/.local/share/opencode/` |

## Related commands

<div class="grid cards" markdown>

-   :material-play-circle: **lerim init**

    ---

    Interactive setup wizard

    [:octicons-arrow-right-24: lerim init](init.md)

-   :material-sync: **lerim sync**

    ---

    Sync sessions after connecting

    [:octicons-arrow-right-24: lerim sync](sync.md)

</div>
