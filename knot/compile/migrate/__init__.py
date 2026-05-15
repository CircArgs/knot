"""Migration emitter — Alembic-autogenerate equivalent for knot.

Introspects a live postgres database via a host-supplied query callable
and diffs against the in-memory ``Spec`` to produce ordered
``MigrationOp`` records that bring the database into alignment.

Sub-modules:
  - ``_introspect`` — pure-read helpers over postgres' system catalogs.
  - ``_diff``       — the diff driver + per-step emission.

The public surface is just ``diff_against_db``, ``MigrationOp``, and
the ``QueryFn`` type alias.
"""

from knot.compile.migrate._diff import MigrationOp, diff_against_db
from knot.compile.migrate._introspect import QueryFn

__all__ = ["MigrationOp", "QueryFn", "diff_against_db"]
