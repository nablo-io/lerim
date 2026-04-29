# Contributing

Thanks for helping with Lerim.

## Summary

The current product contract is DB-only.

Before changing code:

- read `README.md`
- read `src/lerim/README.md`
- read `src/lerim/skills/cli-reference.md`
- read `docs/concepts/how-it-works.md`

## Development setup

```bash
uv venv && source .venv/bin/activate
uv pip install -e '.[test]'
```

## Test expectation

Run the relevant unit tests for your change.
If you touch live runtime behavior, also verify the built service path with
`lerim up --build` before treating the change as release-ready.
