# Quickstart

This is the shortest real path.

## 1. Prepare

```bash
lerim init
lerim connect auto
lerim project add .
```

## 2. Start the service

```bash
lerim up
```

## 3. Check status

```bash
lerim status
lerim status --live
```

## 4. Run the flows

```bash
lerim sync
lerim maintain
lerim ask "What do we already know about the build system?"
```

## 5. Know where data lives

Global Lerim state:

- `~/.lerim/context.sqlite3`
- `~/.lerim/index/sessions.sqlite3`
- `~/.lerim/workspace/`
