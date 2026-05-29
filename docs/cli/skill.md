# lerim skill

Install the bundled Lerim skill into supported agent skill directories.

## Example

```bash
lerim skill install
```

This copies the repo-shipped Lerim skill files into standard agent skill
locations so supported agents can query Lerim before they start work.

## What to do next

Add a short Lerim startup instruction to the repository's agent instruction file,
such as `AGENTS.md`, `CLAUDE.md`, or another tool-specific instruction file.
The instruction should tell agents to use Lerim when past project context may
matter, not as a ritual for every chat.

The recommended startup flow is:

```bash
lerim context-brief show
lerim working-memory show
```

See [Agent Startup Context](../guides/agent-startup-context.md) for the full
install, instruction snippet, and verification flow.
