"""knot.compile — pure spec → SQL emitters.

Each emitter is a free function on the corresponding module:

  - ``knot.compile.ddl.emit_ddl``                 data-plane schema for a spec
  - ``knot.compile.resolver.emit_resolved_view``  per-class resolved CREATE VIEW
  - ``knot.compile.resolver.emit_resolved_views`` all concrete classes' resolved views
  - ``knot.compile.constraints.emit_validation``  per-constraint validation SELECTs
  - ``knot.compile.constraints.emit_validation_union``  single-query UNION ALL form
  - ``knot.compile.write.emit_batch_write``     batched SCD2 binding writes,
                                                  optionally with in-transaction
                                                  constraint enforcement

The spec itself lives in Python code (see ``knot.spec`` / ``knot.expr``).
Migrations diff the in-memory spec against the live postgres schema at
deploy time (Alembic-style autogenerate); knot does not persist the
spec to a meta-table.

Public surface is re-exported here for convenience.
"""

from knot.compile.constraints import emit_validation, emit_validation_union
from knot.compile.ddl import emit_ddl
from knot.compile.expr import compile_sql
from knot.compile.migrate import MigrationOp, diff_against_db
from knot.compile.query import compile_query
from knot.compile.resolver import emit_resolved_view, emit_resolved_views
from knot.compile.trust import emit_trust_seed
from knot.compile.write import (
    BatchWrite,
    ClassWrites,
    emit_assign_canonical,
    emit_batch_write,
    emit_close_out,
    emit_recanonicalize,
)

__all__ = [
    "compile_query",
    "compile_sql",
    "emit_ddl",
    "emit_resolved_view",
    "emit_resolved_views",
    "emit_trust_seed",
    "emit_validation",
    "emit_validation_union",
    "ClassWrites",
    "BatchWrite",
    "emit_batch_write",
    "emit_close_out",
    "emit_assign_canonical",
    "emit_recanonicalize",
    "MigrationOp",
    "diff_against_db",
]
