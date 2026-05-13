"""knot.compile — pure spec → SQL emitters.

Each emitter is a free function on the corresponding module:

  - ``knot.compile.ddl.emit_ddl``         data-plane schema for a spec
  - ``knot.compile.meta.emit_meta_ddl``   spec-storage meta-tables (invariant)
  - ``knot.compile.spec_io.save_spec``    spec → INSERTs into the meta-tables
  - ``knot.compile.spec_io.load_queries`` SELECTs the host runs to fetch a spec
  - ``knot.compile.spec_io.load_spec``    rows → reconstructed Spec

Public surface is re-exported here for convenience.
"""

from knot.compile.ddl import emit_ddl
from knot.compile.meta import emit_meta_ddl
from knot.compile.spec_io import load_queries, load_spec, save_spec

__all__ = ["emit_ddl", "emit_meta_ddl", "save_spec", "load_queries", "load_spec"]
