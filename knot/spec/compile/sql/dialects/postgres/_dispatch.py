"""Central singledispatch registry for expression-tree → SQL compilation.

All predicate handlers register against ``compile_predicate``.  No isinstance
chains outside this module.
"""

from __future__ import annotations

from functools import singledispatch
from typing import Any

from psycopg import sql

from knot.spec.compile.sql.dialects.postgres._context import CompileContext


class CompilerError(Exception):
    """Raised when the compiler encounters an invalid or unsupported tree node."""


@singledispatch
def compile_predicate(node: Any, ctx: CompileContext) -> sql.Composable:
    """Compile an expression-tree node to a ``psycopg.sql.Composable``.

    Parameters are accumulated into ``ctx.params`` as a side-effect.
    Dispatch is by the concrete type of ``node``; all registered handlers live
    in ``_predicate.py`` and ``_relation.py``.
    """
    raise CompilerError(
        f"No SQL handler registered for expression node type "
        f"{type(node).__name__!r}.  Register one via "
        f"@compile_predicate.register."
    )
