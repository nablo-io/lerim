# lerim query

Run deterministic count or list queries over Lerim's local databases.

Use `lerim query` when you want exact retrieval with no LLM synthesis:

- counts
- latest rows
- date-window filters
- `valid_at` checks
- direct inspection of records, versions, or sessions

## Examples

```bash
lerim query records count
lerim query records list --kind decision --limit 10
lerim query records list --valid-at 2026-04-20
lerim query sessions list --created-since 2026-04-20 --created-until 2026-04-20
lerim query sessions list --order-by created_at --limit 20
```

## Notes

- `query` runs locally and does not require the context answerer.
- Session lists only support `created_at` ordering and return newest rows first.
- For explanatory answers, use [lerim answer](answer.md).
- For full argument details, check the bundled CLI reference in `src/lerim/skills/cli-reference.md` in the repo.
