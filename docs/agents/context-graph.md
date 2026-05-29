# Context Graph Agent

The context graph agent runs after curated records are available. It builds a
sparse graph of useful relationships between decisions, constraints, facts,
preferences, references, evidence, and handoffs.

The pipeline below shows the DSPy module flow.

```mermaid
---
config:
  flowchart:
    curve: linear
---
graph TD;
    __start__([<p>__start__</p>]):::first
    load_inventory(load_inventory)
    build_candidates(build_candidates)
    link_records(link_records)
    review_links(review_links)
    persist_graph(persist_graph)
    __end__([<p>__end__</p>]):::last
    __start__ --> load_inventory;
    load_inventory --> build_candidates;
    build_candidates --> link_records;
    link_records --> review_links;
    review_links --> persist_graph;
    persist_graph --> __end__;
    classDef default fill:#f2f0ff,line-height:1.2
    classDef first fill-opacity:0
    classDef last fill:#bfb6fc
```

## Inputs

- active durable records for one project
- semantic-neighbor candidate pairs
- existing graph edges for duplicate avoidance

## Flow

1. `load_inventory` loads active durable records and existing graph edges.
2. `build_candidates` builds semantic-neighbor clusters and candidate
   record pairs.
3. `link_records` asks DSPy to propose sparse, grounded relationships.
4. `review_links` asks DSPy to drop weak, duplicate, or generic links.
5. `persist_graph` writes graph nodes, graph edges, and semantic cluster labels.

## Clustering

The persisted graph stores one durable cluster layer:

- semantic clusters from semantic-neighbor records

A planned hosted dashboard can derive Louvain communities and combined visual
lenses from accepted graph links without adding transient visualization labels
to the local runtime store.

## Output

The graph projection is derived context. Durable records stay canonical.
`context_nodes` and `context_edges` are refreshed from curated records and are
kept ready for clustered graph exploration in the planned hosted product.
