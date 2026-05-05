---
title: 'Translator implementation decision: SPARQL→Ontop, GraphQL→TBD, Cypher→defer for prototype'
status: note
project: knot
tags: [architecture, decisions, plugins, query-translation]
created_at: 2026-04-29T00:50:00+00:00
---

> ⚠️ **LEGACY / UNVERIFIED.** This document was authored during the prototype/exploration phase and has not been reconciled against the current codebase. Treat as historical context, not specification. Cross-check anything you intend to act on. See [`../README.md`](../README.md) for the current entry point.


# Translator implementation decision

Captures evidence-based positions for each `QueryTranslator[Lang]`
plugin so a future reviewer doesn't have to ask "did you check what
already exists?" The user's standing instruction: knot should not
roll its own translator if a good off-the-shelf option exists.

## Per-language landscape

### SPARQL → SQL: **adopt Ontop**

[Ontop](https://ontop-vkg.org/) is the live OSS standard for
"virtual knowledge graph" — translates SPARQL into SQL using
ontology mappings (R2RML / OBDA). Active development, mature, used
in production at scale. This is exactly knot's `SparqlTranslator`
use case. Knot's mapping layer (LinkML class → lake table + columns)
is structurally analogous to R2RML.

**Position:** `SparqlTranslator` default in-tree implementation
wraps Ontop. The wrapper translates knot's materialization map into
R2RML, calls Ontop's library to translate the SPARQL query against
the lake's SQL dialect, returns the SQL string. Risk: Ontop's
licensing (Apache 2.0 — fine) and JVM dependency (knot is Python;
will need a subprocess or sidecar — manageable but not free).

Sibling tools considered + dismissed:
- **Morph-RDB** — academic, less actively maintained.
- **D2RQ** — older, not maintained.
- **Stardog** — commercial, full KG platform; not a library.

### GraphQL → SQL: **decision deferred**

The OSS landscape conflates *executors* and *translators*:

- **Hasura** — auto-generates a GraphQL endpoint that hits Postgres
  directly. Executes; doesn't return SQL strings. SQL-gen is
  internal and not a stable public API.
- **PostGraphile** — same shape as Hasura.
- **Ontop GraphQL face** — uses the same R2RML mapping; a candidate
  if we adopt Ontop for SPARQL anyway.

**Position:** when knot needs a GraphQL translator, evaluate two
options: (a) extend the Ontop adoption to its GraphQL face — minimal
incremental investment if SPARQL is already wired; (b) write a thin
in-house translator over `sqlglot` AST against the LinkML schema +
materialization map. Decide at implementation time based on whether
GraphQL becomes a high-priority surface. Not a v1 priority for the
prototype.

### Cypher → SQL: **defer; build later**

No good OSS translator exists. Apache AGE adds Cypher *execution* as
a Postgres extension (not a translator). Memgraph is its own DB.
Academic translators (CypherSQL, etc.) aren't productionized.

**Position:** Cypher → SQL stays in the architecture as a planned
plugin slot but is **not implemented for the prototype**. The slot
remains in the protocol catalogue (`CypherTranslator` is part of the
`QueryTranslator[Lang]` family), and a future implementation rolls
its own — defensible because the gap in OSS is real and well-known.
Until then, `CypherTranslator` raises `NotImplementedError` with a
message pointing to this doc.

## Architectural posture

This decision lives entirely behind the `QueryTranslator[Lang]`
protocol — the plugin pattern absorbs the heterogeneity of upstream
choices. Adopting Ontop for SPARQL doesn't bind knot to JVM tooling
broadly; it's one plugin's implementation detail. Building Cypher
in-house when the time comes doesn't require revisiting the
SPARQL choice.

## Action items

- [ ] When `query-execution-endpoints` lands, the translator
  dispatch must call into the registered translator plugin per
  language. The protocol surface accommodates "this translator is
  not implemented" cleanly (404 or 501 with a remediation message).
- [ ] When the SPARQL surface becomes a real priority, file a task
  `sparql-translator-via-ontop` covering: subprocess vs library
  wrapping, R2RML mapping generation from LinkML+materialization
  map, version pinning, error translation.
- [ ] Document the Cypher gap in user-facing docs once the
  workbench ships, so curators don't expect a feature that
  intentionally doesn't exist.

## References

- `knot-architecture-v1.md` § "The protocol catalogue" entry 5
  (`QueryTranslator[Lang]`).
- User instruction (2026-04-29): "knot shouldn't roll its own if
  good off the shelf ones exist."
- User instruction (2026-04-29): "some things like cypher to sql we
  can leave in the design but don't need to roll for knot's
  prototype."
