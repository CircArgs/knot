"""knot.compile — pure spec → SQL emitters.

Each emitter is a free function on the corresponding module:

  - ``knot.compile.ddl.emit_ddl``         data-plane schema for a spec
  - ``knot.compile.meta.emit_meta_ddl``   spec-storage meta-tables (invariant)

Public surface is re-exported here for convenience.
"""

from knot.compile.ddl import emit_ddl
from knot.compile.meta import emit_meta_ddl

__all__ = ["emit_ddl", "emit_meta_ddl"]
