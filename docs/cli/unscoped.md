# lerim unscoped

List indexed sessions that do not currently match any registered project.

This command is useful when new traces are being indexed but not extracted because project scope resolution has no matching project.

## Examples

```bash
lerim unscoped
lerim unscoped --limit 100
lerim unscoped --json
```

## Notes

- `unscoped` uses the running local API, so it requires `lerim up` or `lerim serve`.
- Register the missing repo with `lerim project add ...` when the sessions should be in scope.
