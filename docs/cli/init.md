# lerim init

Interactive setup wizard for first-time Lerim configuration.

## Overview

Run this once after installing Lerim. It detects your installed coding-agent trace sources, lets you select which to connect, and writes the initial config to `~/.lerim/config.toml`.

!!! info "Host-only command"
    This command runs on the host machine. It does not require a running Lerim server.

## Syntax

```bash
lerim init
```

## What it does

1. Scans for installed coding-agent trace sources (Claude Code, Codex CLI, Cursor, OpenCode)
2. Prompts you to select which sources to connect
3. Writes the initial config to `~/.lerim/config.toml`
4. Checks for Docker availability
5. Prints next steps

## Example

```bash
lerim init
```

Output:

```
Welcome to Lerim.

Which coding-agent trace sources do you use?
  claude (detected) [Y/n]: y
  cursor (detected) [Y/n]: y
  codex (not found) [y/N]: n
  opencode (not found) [y/N]: n

Config written to ~/.lerim/config.toml
Agents: claude, cursor

Docker: found

Next steps:
  lerim project add /path/to/repo   # register a project
  lerim up                           # start the Docker service
```

## Notes

- Running `lerim init` again overwrites the existing config
- API keys are not stored in config — they come from environment variables
- You can manually edit `~/.lerim/config.toml` after init

## Related commands

<div class="grid cards" markdown>

-   :material-folder-plus: **lerim project add**

    ---

    Register a project after init

    [:octicons-arrow-right-24: lerim project](project.md)

-   :material-connection: **lerim connect**

    ---

    Manage platform connections

    [:octicons-arrow-right-24: lerim connect](connect.md)

</div>
