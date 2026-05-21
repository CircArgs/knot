"""knot_graphql — GraphQL adapter for knot specs.

Takes a ``knot.Spec`` and emits a GraphQL surface: one SDL string + a
``Resolvers`` class whose methods the host wires into whatever GraphQL
server framework it chooses (Ariadne, Strawberry, Graphene,
graphql-core, Apollo Federation, …).

knot_graphql is a **reference adapter** — it sits on top of the knot
substrate and depends on it, but knot does not depend on knot_graphql.
No GraphQL framework dependency; no DB driver dependency. The host
injects both via a ``sql_executor`` callable.

Quick start::

    from knot_graphql import build_schema

    sdl, resolvers = build_schema(spec, sql_executor=pg_execute)
    # sdl  → one SDL string to hand to your GraphQL server
    # resolvers → Resolvers instance — call its methods from your
    #             framework's resolver hooks

Public surface
--------------
``build_schema(spec, sql_executor, *, schema=None)``
    One-shot entry point.  Returns ``(sdl, resolvers)``.

``emit_sdl(spec)``
    Return the SDL string alone (useful for schema-introspection tools
    or offline SDL generation).

``Resolvers(spec, sql_executor, *, schema=None)``
    Resolver class.  Methods:
    - ``resolve_by_id(class_name, canonical_id)``
    - ``resolve_list(class_name, *, first, after, filters)``
    - ``resolve_fk_walk(parent, fk_slot_name)``
    - ``resolve_reverse_count(parent, primary_class_name, fk_slot_name, ref_class_name)``

``projection_from_selection(field_names, cls)``
    Turn a list of requested field names into ``cls.col.<name>`` Refs.

Known gaps (v0 — post v0 roadmap)
----------------------------------
- **Mutations / write-path resolvers** — derive from
  ``binding.write_sql()``, ``binding.assign_canonical_sql()``, etc.
  Left to the host or a v1 extension.
- **DataLoader integration** — framework-specific; the host wraps
  ``resolve_fk_walk`` / ``resolve_reverse_count`` with its DataLoader.
- **Subscriptions** — requires a pub/sub runtime the host owns.
- **Federation directives** — ``@key``, ``@external``, etc. are
  host-specific; emit SDL + annotate manually.
- **Auth / authz** — the host wraps resolver methods with its auth
  middleware.
- **Custom scalar registration** — ``Date``, ``DateTime``, ``Vector``,
  etc. require framework-specific scalar objects; the host registers
  them and maps the SDL ``String`` / ``[Float!]!`` fields accordingly.
- **Strawberry / Graphene / Ariadne bindings** — deliberately
  framework-agnostic; this package ships SDL + resolver dict only.
"""

from knot_graphql.codegen import emit_sdl
from knot_graphql.resolvers import Resolvers, projection_from_selection
from knot_graphql.schema import build_schema
