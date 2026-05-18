"""knot.compile — pure spec → SQL emitters.

Each emitter is a free function on the corresponding module:

  - ``knot.compile.ddl.emit_ddl``                 data-plane schema for a spec
  - ``knot.compile.resolver.emit_resolved_view``  per-class resolved CREATE VIEW
  - ``knot.compile.resolver.emit_resolved_views`` all concrete classes' resolved views
  - ``knot.compile.constraints.emit_validation``  per-constraint validation SELECTs
  - ``knot.compile.constraints.emit_validation_union``  single-query UNION ALL form
  - ``knot.compile.write.emit_binding_write_sql`` SCD2 write SQL templates
                                                  for one binding (close-out + insert)

knot is a *compiler*: ``Spec.ddl(schema=…)`` emits the canonical
target schema. Schema migrations against a live DB are delegated to
external tools (sqldef / Atlas / dbmate / …); see CLAUDE.md
§"Schema deployment".

Public surface is re-exported here for convenience.
"""

from knot.compile.constraints import emit_validation, emit_validation_union
from knot.compile.ddl import emit_ddl
from knot.compile.expr import compile_sql
from knot.compile.query import compile_query
from knot.compile.resolver import (
    emit_all_sources_view,
    emit_all_sources_views,
    emit_resolved_view,
    emit_resolved_views,
)
from knot.compile.weight import emit_weight_seed
from knot.compile.write import (
    emit_assign_canonical_sql,
    emit_binding_write_sql,
    emit_close_out_sql,
    emit_recanonicalize_sql,
)
