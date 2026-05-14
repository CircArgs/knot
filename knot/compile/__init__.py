"""knot.compile — pure spec → SQL emitters.

Each emitter is a free function on the corresponding module:

  - ``knot.compile.ddl.emit_ddl``                 data-plane schema for a spec
  - ``knot.compile.resolver.emit_resolved_view``  per-class resolved CREATE VIEW
  - ``knot.compile.resolver.emit_resolved_views`` all concrete classes' resolved views
  - ``knot.compile.constraints.emit_validation``  per-constraint validation SELECTs
  - ``knot.compile.constraints.emit_validation_union``  single-query UNION ALL form
  - ``knot.compile.data_io.emit_batch_write``     batched SCD2 binding writes,
                                                  optionally with in-transaction
                                                  constraint enforcement

The spec itself lives in Python code (see ``knot.spec`` / ``knot.expr``).
Migrations diff the in-memory spec against the live postgres schema at
deploy time (Alembic-style autogenerate); knot does not persist the
spec to a meta-table.

Public surface is re-exported here for convenience.
"""

from knot.compile.constraints import emit_validation, emit_validation_union
from knot.compile.data_io import BatchWrite, ClassWrites, emit_batch_write
from knot.compile.ddl import emit_ddl
from knot.compile.resolver import emit_resolved_view, emit_resolved_views

__all__ = [
    "emit_ddl",
    "emit_resolved_view",
    "emit_resolved_views",
    "emit_validation",
    "emit_validation_union",
    "ClassWrites",
    "BatchWrite",
    "emit_batch_write",
]
