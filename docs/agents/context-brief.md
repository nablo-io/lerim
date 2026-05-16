# Context Brief Agent

The context brief agent compiles startup context from stored records so a new
agent session can begin with compact project memory.

The graph below is generated from the compiled LangGraph runtime.

```mermaid
---
config:
  flowchart:
    curve: linear
---
graph TD;
    __start__([<p>__start__</p>]):::first
    prepare_candidates(prepare_candidates)
    compile_brief(compile_brief)
    build_draft(build_draft)
    __end__([<p>__end__</p>]):::last
    __start__ --> prepare_candidates;
    compile_brief --> build_draft;
    prepare_candidates --> compile_brief;
    build_draft --> __end__;
    classDef default fill:#f2f0ff,line-height:1.2
    classDef first fill-opacity:0
    classDef last fill:#bfb6fc
```

## Inputs

- recent and important context records for one project
- freshness metadata
- existing brief state

## Flow

1. `prepare_candidates` shapes context records for the compiler.
2. `compile_brief` selects and organizes startup context.
3. `build_draft` writes the generated markdown artifact.

## Output

The generated `CONTEXT_BRIEF.md` is a derived startup artifact. It is not the
durable context store.
