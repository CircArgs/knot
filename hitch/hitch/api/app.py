"""FastAPI + Ariadne — exposes knot_graphql's SDL + resolvers as a
real running GraphQL endpoint.

knot_graphql gives us:
  - one SDL string (every concrete + virtual class + Query root)
  - a Resolvers instance with resolve_by_id, resolve_list,
    resolve_fk_walk, resolve_reverse_count

Ariadne wraps each into a field resolver. The Query root dispatches
the by-id / list fields; per-type FK + reverse-count fields are
attached via ObjectType resolvers.
"""

from __future__ import annotations

import logging
from typing import Any

from ariadne import (
    ObjectType,
    QueryType,
    make_executable_schema,
)
from ariadne.asgi import GraphQL
from fastapi import FastAPI
from knot.ast.types import ClassRef
from knot_graphql import build_schema as build_knot_schema

from hitch import config
from hitch.db import execute
from hitch.spec import build_spec

log = logging.getLogger("hitch.api")


def _camel(name: str) -> str:
    return name[0].lower() + name[1:]


def _pascal(name: str) -> str:
    return name[0].upper() + name[1:] if name else name


def make_app() -> FastAPI:
    cfg = config.load()
    spec = build_spec(schema=cfg.pg_schema, embedding_dim=cfg.embedding_dim)

    def executor(sql: str) -> list[dict[str, Any]]:
        return execute(cfg.pg_dsn, sql)

    sdl, knot_resolvers = build_knot_schema(spec, executor)

    # ----- Query root --------------------------------------------------
    query = QueryType()

    def _make_by_id(cls_name: str):
        async def resolver(_, info, canonicalId):
            row = knot_resolvers.resolve_by_id(cls_name, canonicalId)
            if row is not None:
                row["__class_name"] = cls_name
            return row

        return resolver

    def _make_list(cls_name: str):
        async def resolver(_, info, first=20, after=None):
            rows = knot_resolvers.resolve_list(cls_name, first=first, after=after)
            for r in rows:
                r["__class_name"] = cls_name
            return rows

        return resolver

    type_objs: list[ObjectType] = []
    for cls in list(spec.concrete_classes()) + list(spec.virtual_classes()):
        query.set_field(_camel(cls.name), _make_by_id(cls.name))
        query.set_field(f"{_camel(cls.name)}List", _make_list(cls.name))

    # ----- Per-type FK walk + reverse-count resolvers ------------------
    # FK walks: register against both concrete and virtual class types
    # (virtuals share the concrete root's slots, so the same resolver
    # logic works). Reverse-count fields are only emitted on concrete
    # types by knot_graphql, so those resolvers stay concrete-only.
    fk_targets = [(cls.name, cls) for cls in spec.concrete_classes()]
    fk_targets += [(vc.name, vc.concrete_root()) for vc in spec.virtual_classes()]

    for type_name, cls in fk_targets:
        ot = ObjectType(type_name)

        for slot in cls.effective_slots():
            if isinstance(slot.type, ClassRef):
                target_name = slot.type.target.name
                slot_name = slot.name

                def _walk(parent, info, _slot_name=slot_name, _target=target_name):
                    row = knot_resolvers.resolve_fk_walk(parent, _slot_name)
                    if row is not None:
                        row["__class_name"] = _target
                    return row

                ot.set_field(slot_name, _walk)

        # Reverse-count fields only exist on concrete types in the SDL.
        if type_name == cls.name:  # cls.name == type_name only for concrete
            for ref_cls, ref_slot in cls.referrers:
                count_name = f"{_camel(ref_cls.name)}{_pascal(ref_slot.name)}Count"
                primary_name = cls.name
                ref_name = ref_cls.name
                slot_name = ref_slot.name

                def _rcount(
                    parent,
                    info,
                    _primary=primary_name,
                    _ref=ref_name,
                    _slot=slot_name,
                ):
                    return knot_resolvers.resolve_reverse_count(
                        parent, _primary, _slot, _ref
                    )

                ot.set_field(count_name, _rcount)

        type_objs.append(ot)

    schema_obj = make_executable_schema(sdl, query, *type_objs)

    app = FastAPI(title="hitch — knot + temporal + graphql demo")
    app.mount("/graphql", GraphQL(schema_obj, debug=True))

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/sdl")
    async def get_sdl() -> dict[str, str]:
        return {"sdl": sdl}

    return app


app = make_app()
