"""Graph router — data plane.

Distinct from the spec router (which authors meaning): this router pushes,
reads, and corrects facts in the graph. No draft lifecycle — every mutation
validates against the *currently published* spec and lands directly.

Endpoints under ``/graph``:

  - ``ingest.py``      POST  /graph/ingest/{source}                  push source rows
  - ``reads.py``       GET   /graph/classes/{class}                  list rows
                       GET   /graph/classes/{class}/{cid}            contributions
                       GET   /graph/classes/{class}/{cid}/resolved   trust-resolved
  - ``trust.py``       GET   /graph/trust                            list scores
                       GET   /graph/trust/{source}                   score for source
                       PUT   /graph/trust/{source}                   set score
                       GET   /graph/trust/posteriors                 list posteriors
                       GET   /graph/trust/posteriors/{src}/{slot}    posterior
                       DELETE …                                      reset posterior
                       POST  /graph/trust/feedback                   bandit observation
  - ``corrections.py`` POST  /graph/corrections                      typed corrections
                       GET   /graph/corrections                      audit log
  - ``graphql.py``     POST  /graph/query                            ontology-shaped query
  - ``constraints.py`` POST  /graph/constraints/check                run all constraints

Storage shape (locked, see ``knot.db.migration``): per-class postgres
tables; columns mirror stored slots; primary key
``(_source, _source_row_id)``; SCD2 bindings carry ``canonical_id``,
``valid_from``, ``valid_to``, ``change_type``, ``applied_revision``.
"""

from __future__ import annotations

from fastapi import APIRouter

from knot.api.graph import constraints, corrections, graphql, ingest, reads, trust


router = APIRouter(prefix="/graph", tags=["graph"])
router.include_router(ingest.router)
router.include_router(reads.router)
router.include_router(trust.router)
router.include_router(corrections.router)
router.include_router(graphql.router)
router.include_router(constraints.router)
