"""GraphQL mutations — write-path resolvers wrapping the
``_user_corrections`` binding.

Every concrete class gets one mutation:

    correct<Class>(canonical_id: ID!, source_identifier: String!,
                   <slot>: <type>, ...): <Class>

Plus one universal mutation:

    retract(class_name: String!, source_identifier: String!): Boolean

The mutation upserts a row into the corrections binding's bindings
table. Because ``_user_corrections`` is weighted 1e6 in the demo's
deploy script, the resolver argmax picks the correction's value
over any declared source on the next read. The mutation returns the
class type so clients can request the post-correction shape inline.

knot_graphql itself doesn't ship mutation emission — it explicitly
flags this as host territory in its README. hitch owns the shape.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ariadne import ObjectType
from knot.ast.types import ClassRef
from knot.spec import OntologyClass, Spec
from knot_graphql.types import gql_type


def _arg_type(slot, *, class_name: str) -> str:
    """SDL type for a mutation arg — always nullable so clients can
    override any subset of slots."""
    if isinstance(slot.type, ClassRef):
        return "ID"
    t = gql_type(slot.type, required=False, class_name=class_name, slot_name=slot.name)
    # Array / Vector / Enum branches in gql_type._inner emit a required
    # outer wrapper (e.g. ``[String!]!``); for mutation args we want
    # nullable so partial-update mutations don't force every list slot.
    if t.endswith("!"):
        t = t[:-1]
    return t


def emit_mutation_sdl(spec: Spec) -> str:
    """Return the SDL fragment for the Mutation type — one
    correct<Class> per concrete class plus retract."""
    from knot.ast.types import Vector

    lines: list[str] = ["type Mutation {"]
    for cls in spec.concrete_classes():
        args = ["canonical_id: ID!", "source_identifier: String!"]
        for slot in cls.effective_slots():
            if slot.identifier or isinstance(slot.type, Vector):
                # Identifier slots map to canonical_id; vector slots
                # are owned by the embedding worker.
                continue
            args.append(f"{slot.name}: {_arg_type(slot, class_name=cls.name)}")
        args_csv = ", ".join(args)
        lines.append(f"  correct{cls.name}({args_csv}): {cls.name}!")
    lines.append(
        "  retract(class_name: String!, canonical_id: ID!, "
        "source_identifier: String!): Boolean!"
    )
    lines.append("}")
    return "\n".join(lines)


def build_mutation_resolvers(
    spec: Spec,
    sql_executor: Callable[[str, dict[str, Any] | None], list[dict[str, Any]]],
    by_id: Callable[[str, str], dict[str, Any] | None],
) -> ObjectType:
    """Wire one resolver per ``correct<Class>`` mutation plus retract.

    ``sql_executor`` runs an SQL string with an optional params dict;
    we use it to call ``corrections_binding.write_sql()`` (binding the
    rows JSON) and ``binding.retract_sql()`` (binding the keys).

    ``by_id`` is the host's ``Resolvers.resolve_by_id`` so the mutation
    can return the post-write resolved row in one round-trip.
    """
    import json

    mutation = ObjectType("Mutation")

    def _make_correct(cls: OntologyClass):
        corr = cls.corrections_binding()
        sql = corr.write_sql()
        slot_names = {s.name for s in cls.effective_slots() if not s.identifier}
        # Vector slots aren't writable via the mutation surface (see emit_mutation_sdl).
        from knot.ast.types import Vector

        vector_slots = {
            s.name for s in cls.effective_slots() if isinstance(s.type, Vector)
        }
        writable = slot_names - vector_slots

        async def resolver(_, info, canonical_id, source_identifier, **overrides):
            row = {
                "canonical_id": canonical_id,
                "source_identifier": source_identifier,
            }
            for k, v in overrides.items():
                if k in writable and v is not None:
                    row[k] = v
            sql_executor(sql, {"rows": json.dumps([row])})
            after = by_id(cls.name, canonical_id)
            if after is not None:
                after["__class_name"] = cls.name
            return after

        return resolver

    for cls in spec.concrete_classes():
        mutation.set_field(f"correct{cls.name}", _make_correct(cls))

    async def retract_resolver(_, info, class_name, canonical_id, source_identifier):
        cls = spec.classes.get(class_name)
        if not isinstance(cls, OntologyClass):
            raise ValueError(f"unknown class {class_name!r}")
        corr = cls.corrections_binding()
        sql_executor(
            corr.retract_sql(),
            {"canonical_id": canonical_id, "source_identifier": source_identifier},
        )
        return True

    mutation.set_field("retract", retract_resolver)
    return mutation
