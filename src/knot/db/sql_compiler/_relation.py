"""Relation-traversal handler stubs.

These nodes require cross-class JOIN logic that lands with the /graph/query
slice.  Registering them here ensures the dispatcher always resolves to a
handler (no "no handler registered" CompilerError); the handler itself raises
NotImplementedError with a clear, actionable message.
"""

from __future__ import annotations

from psycopg import sql

from knot.ontology.metaschema import (
    FilteredRelation,
    FormatDerivation,
    RecursiveTraversal,
    RelationAggregate,
    RelationAll,
    RelationAny,
    RelationCount,
    RelationFirst,
    RelationProject,
    RelationRef,
    ScalarDerivation,
)

from knot.db.sql_compiler._context import CompileContext
from knot.db.sql_compiler._dispatch import compile_predicate


def _stub(name: str):
    def _handler(node, ctx: CompileContext) -> sql.Composable:
        raise NotImplementedError(
            f"{name} compilation lands with /graph/query (cross-class JOIN "
            "logic not implemented in this slice).  Use Within / Compare / "
            "BoolExpr / Between / Matches for within-class constraint predicates."
        )
    return _handler


compile_predicate.register(RelationAll)(_stub("RelationAll"))
compile_predicate.register(RelationAny)(_stub("RelationAny"))
compile_predicate.register(RelationFirst)(_stub("RelationFirst"))
compile_predicate.register(FilteredRelation)(_stub("FilteredRelation"))
compile_predicate.register(RelationRef)(_stub("RelationRef"))
compile_predicate.register(RelationProject)(_stub("RelationProject"))
compile_predicate.register(RelationCount)(_stub("RelationCount"))
compile_predicate.register(RelationAggregate)(_stub("RelationAggregate"))
compile_predicate.register(ScalarDerivation)(_stub("ScalarDerivation"))
compile_predicate.register(FormatDerivation)(_stub("FormatDerivation"))
compile_predicate.register(RecursiveTraversal)(_stub("RecursiveTraversal"))
