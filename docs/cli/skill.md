# lerim skill

Install Lerim's bundled skill, register instruction artifacts, and review
evidence-backed skill update proposals.

## Install

```bash
lerim skill install
```

This copies the repo-shipped Lerim skill files into standard agent skill
locations so supported agents can query Lerim before they start work.

## Register Targets

Register any skill or instruction artifact Lerim should monitor:

```bash
lerim skill target add ~/.agents/skills/clean-code --description "Keep simplification guidance current"
lerim skill target list
lerim skill target show it_abc123
```

Supported targets include skill directories, `SKILL.md`, `AGENTS.md`,
`CLAUDE.md`, `GEMINI.md`, and related instruction files. Proposal paths are
limited to files scanned as part of the registered target; skill bundles may
also propose new files under `references/`, `reference/`, or `examples/`.
Targets inside a registered Lerim project are scoped to that project. Targets
outside registered projects are global and may learn from registered project
records.

## Auto-Apply

Targets default to review mode. Auto-apply is opt-in and remains bounded by the
target policy:

```bash
lerim skill target auto-apply it_abc123 --enable --risk low
lerim skill target auto-apply it_abc123 --disable
```

Automatic application only runs for pending proposals that pass validation,
guard checks, risk limits, changed-file limits, added-line limits, and
removed-line limits. Scripts, assets, config files, and frontmatter stay blocked
unless the target policy explicitly allows them.

## Refresh And Review

Run a scan to compile proposals from scoped context records:

```bash
lerim skill refresh it_abc123
lerim skill refresh clean-code --record-limit 120 --json
```

Review proposals before applying or rejecting them:

```bash
lerim skill proposal list
lerim skill proposal list --target-id it_abc123 --status pending_review
lerim skill proposal show ip_abc123
lerim skill proposal apply ip_abc123
lerim skill proposal reject ip_abc123
```

Applying is allowed only for pending, guard-accepted, validation-passing
proposals whose file baselines still match the current target files. Terminal
proposals such as applied or rejected proposals cannot be edited back into
review.

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
