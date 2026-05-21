"""One-shot glue: spec → (sdl, Resolvers).

The host wires the returned pair into whatever GraphQL server framework
it uses::

    from knot_graphql import build_schema

    sdl, resolvers = build_schema(spec, sql_executor=pg_execute)
    # hand sdl + resolvers to Ariadne / Strawberry / graphql-core / …
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from knot.spec import Spec
from knot_graphql.codegen import emit_sdl
from knot_graphql.resolvers import Resolvers


def build_schema(
    spec: Spec,
    sql_executor: Callable[[str], list[dict[str, Any]]],
    *,
    schema: str | None = None,
) -> tuple[str, Resolvers]:
    """Return the SDL string and a ``Resolvers`` instance bound to ``spec``.

    Parameters
    ----------
    spec:
        A validated knot ``Spec``.
    sql_executor:
        Callable ``(sql: str) -> list[dict]``.  The host injects the DB
        adapter — no driver dependency inside ``knot_graphql``.
    schema:
        Override the postgres schema name.  Defaults to ``spec.schema``.

    Returns
    -------
    tuple[str, Resolvers]
        ``(sdl, resolvers)`` — wire into your GraphQL framework of choice.
    """
    sdl = emit_sdl(spec)
    resolvers = Resolvers(spec, sql_executor, schema=schema)
    return sdl, resolvers
