# Installation

Install Lerim, connect your agent traces, and start the service.

## Install

```bash
pip install lerim
```

## Initialize

```bash
lerim init
```

This writes user config to the active Lerim config path (by default `~/.lerim/config.toml`).

## Connect agents

```bash
lerim connect auto
```

Or connect one platform manually:

```bash
lerim connect claude
lerim connect codex
```

## Register a project

```bash
lerim project add .
```

This only registers the repo path.

## Start Lerim

```bash
lerim up
```

Or run the server directly:

```bash
lerim serve
```

If you run `lerim serve` directly instead of `lerim up`, restart it after
changing registered projects or config that affects scope or runtime mounts.
