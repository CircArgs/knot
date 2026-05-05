---
title: Agents need first-class access to the ontology and to query through it
status: note
created_at: 2026-04-29T13:05:00+00:00
project: knot
tags: [design, control-plane, agents, ontology, value-prop]
---

> ⚠️ **LEGACY / UNVERIFIED.** This document was authored during the prototype/exploration phase and has not been reconciled against the current codebase. Treat as historical context, not specification. Cross-check anything you intend to act on. See [`../README.md`](../README.md) for the current entry point.


## Summary

A key value prop of knot's control plane (often missed in the
"curator dashboard" framing): the ontology is **surfaceable data**
that downstream agents need to consume programmatically, not just
something humans browse.

The control plane unlocks accessibility that ad-hoc projects don't
have:
- An agent can fetch the ontology in **the form it needs** —
  LinkML YAML, JSON Schema, GraphQL SDL, OpenAPI shapes, RDF/SHACL,
  Cypher schema, etc.
- The agent can then **query through it** — i.e., the ontology
  drives type-safe, schema-aware queries against the materialized
  data. The agent doesn't need to know table layouts, just classes
  and slots.

This is a generalization of "data discovery" to "ontology-driven
data discovery + query authoring."

## Implications for design

- **Ontology export endpoints must be first-class API.** Not just
  the UI's `OntologyPage` — `/ontology/export?format=jsonschema`,
  `/ontology/export?format=linkml`, `/ontology/{class}/schema?format=...`
  are agent-facing surfaces.
- **MCP/tool integration is the natural agent path.** The cletus
  vault MCP pattern shows how. knot should expose:
  - `knot_ontology_get_class(name, format='linkml'|'jsonschema'|...)`
  - `knot_ontology_list_classes(filter=...)`
  - `knot_query(language, body, ontology_aware=true)` —
    server-side validates against ontology before dispatch
  - `knot_entity_get(class, key)` — typed by ontology
- **Query workbench is human-first; agent-first equivalents exist
  via the same backend endpoints.** Same auth, same trust scoring.
- **The 7 ontology kinds (data/source/pipeline/er_strategy/dq_check/
  trust_policy/materialization_target) are the agent's API surface
  for the *control plane itself*.** An agent should be able to ask
  knot: "what sources contribute to Movie?", "what's the active
  trust policy for source imdb?" — and then act on the answers.

## How to apply

- When reviewing the OntologyPage UI, also evaluate: does an agent
  reading the same data over the API have parity? Visible-but-
  unqueryable info is a red flag.
- When designing new endpoints, ask: "is this useful to agents,
  or only humans?" Build agent-shaped endpoints first; the UI
  becomes a thin layer.
- Architecture review should explicitly check that
  ontology-discovery + query-through-ontology is a documented
  control-plane capability, not an emergent property of having a
  schemas page.

## Why we wrote this down

Author's note (user, 2026-04-29): the control plane's distinct
value vs. ad-hoc data systems is *agent-accessibility of the
ontology*. Easy to forget when staring at curator-facing
screenshots. Don't lose this thread when designing review and
v1.
