# lerim retry / lerim skip

Manage dead-letter queue jobs.

Use these commands when extraction jobs are blocked and you need to retry them or mark them done.

## Examples

```bash
lerim retry <run_id>
lerim retry --all
lerim retry --project lerim-cli

lerim skip <run_id>
lerim skip --all
lerim skip --project lerim-cli
```

## Notes

- These commands operate on dead-letter queue entries.
- Use [lerim queue](queue.md) first to inspect what is blocked.
