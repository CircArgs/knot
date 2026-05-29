# Docs Index

Design notes, position papers, and architectural docs for knot and the surrounding stack.

## Position Papers

| Doc | Topic |
|---|---|
| [ontology-as-code.md](./ontology-as-code.md) | Why UI-driven ontology editing is an antipattern. Schema lives in git. |

## Architecture

| Doc | Topic |
|---|---|
| [postgres-graph-modeling.md](./postgres-graph-modeling.md) | Two postgres data models for the graph: FK-on-class vs universal bridge. Mermaid diagrams, pros/cons, verdict for shallow-traversal workloads. |
| [postgres-graph-modeling-extended.md](./postgres-graph-modeling-extended.md) | Deeper analysis: SQL/PGQ alignment, scenario-by-scenario comparison, augmentations (relation tracking, partitioning, materialized edges view), codegen, schema-first pitch. |
| [api-dto-sync.md](./api-dto-sync.md) | Keeping API DTOs, JPA entities, and the DB model in sync. MapStruct for writes, JPA projections for reads. |

## Reference

| Doc | Topic |
|---|---|
| [naming-glossary.md](./naming-glossary.md) | Canonical names for class, binding, canonical, matching, trust, etc. Aliases to retire from team communication. |

## Existing

| Doc | Topic |
|---|---|
| [PITCH.md](./PITCH.md) | The knot pitch document. |
| [temporal-adapter.md](./temporal-adapter.md) | Notes on the Temporal adapter pattern. |
