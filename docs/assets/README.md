# Public Asset Notes

These files are public documentation assets. Keep this folder limited to assets
that are safe to ship with the docs site.

- `lerim.png` is the docs-site logo and favicon copy.
- `lerim-context-compiler.svg` is the public README hero showing completed
  runs flowing into Lerim, then into a context graph reused by agents and
  humans.
- `lerim-context-retrieval.svg` is the public README visual showing CLI, skill,
  and MCP retrieval from the context graph.
- `lerim-custom-trace-folder.svg` is the public custom-trace flow showing
  clean JSONL registration and daemon polling ingest. Trace-source labels use
  support/research/custom-harness examples to reflect the multi-vertical custom path.
- `lerim-agent-improvement-loop.png` is the public improvement-loop diagram:
  your agent → Lerim open core (capture, compile, cite) → cited context served
  back → Lerim private layer (specialization). Used on the README and the Custom
  & Non-Coding Agents guide.
- `integration-matrix-screenshot.png` is a non-authoritative public docs
  screenshot, not a benchmark artifact. Regenerate it from the integration
  matrix page before release.
- `benchmark-summary.svg` is for benchmark docs only. Keep a clear provenance
  note beside it until the raw artifacts are rerun from a clean release commit
  and pass the clean validator.
