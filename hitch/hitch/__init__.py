"""hitch — end-to-end reference app on top of the knot substrate.

Wires knot + knot_graphql + temporalio + sentence-transformers into a
single runnable demo:

  postgres (pgvector) ◀────────────┐
       ▲                            │
       │                            │
  hitch-worker (Temporal)      hitch-api (FastAPI + Ariadne)
       │                            │
       ├── ingest workflows          └── GraphQL resolvers via
       ├── embedding backfill            knot_graphql
       ├── ER workflow
       └── validation sweep

hitch is NOT part of knot — it's an application that consumes the
substrate. Everything DB-related goes through knot SQL emitters;
hitch never hand-writes SQL.
"""

from hitch.spec import build_spec

__all__ = ["build_spec"]
