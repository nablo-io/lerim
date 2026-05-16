# lerim queue

Inspect the local session-processing queue.

Use `lerim queue` to debug ingestion state without involving the context answerer.

## Examples

```bash
lerim queue
lerim queue --failed
lerim queue --status pending
lerim queue --project lerim-cli
lerim queue --json
```

## Notes

- `queue` is host-only and reads the local session catalog.
- Use [lerim retry / lerim skip](retry-skip.md) to unblock dead-letter jobs.
