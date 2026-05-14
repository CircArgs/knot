"""Spec persistence — save a ``Spec`` to the meta-tables and reassemble
it from rows fetched back out.

Pure functions. The host runs the SQL; this module returns parameterized
statements (psycopg-style ``%s`` placeholders) and a row-to-``Spec``
reassembler.

Workflow:

  1. ``save_spec(spec)`` → list of ``(sql, params)`` INSERTs. Host runs
     them inside a transaction.
  2. ``load_queries(spec_id, version)`` → ``{table_name: (sql, params)}``
     SELECTs. Host runs each, collects rows.
  3. ``load_spec({table_name: rows})`` → ``Spec``.
"""

from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

from knot.expr import Expr
from knot.spec import (
    Array,
    ClassRef,
    Constraint,
    OntologyClass,
    Primitive,
    Slot,
    Source,
    SourceBinding,
    SourceMap,
    Spec,
    TypeExpression,
    VirtualClass,
)


# ---------------------------------------------------------------------------
# TypeExpression <-> JSON (jsonb column shape)
# ---------------------------------------------------------------------------


def type_to_json(t: TypeExpression) -> dict[str, Any]:
    """Encode a ``TypeExpression`` as the jsonb shape stored in
    ``knot_meta.slots.type``."""
    if isinstance(t, Primitive):
        return {"kind": "primitive", "value": t.value}
    if isinstance(t, Array):
        return {"kind": "array", "of": type_to_json(t.of)}
    if isinstance(t, ClassRef):
        return {"kind": "class_ref", "target": t.target.name}
    raise TypeError(f"unhandled type: {type(t).__name__}")


def type_from_json(
    d: Mapping[str, Any],
    classes: Mapping[str, OntologyClass],
) -> TypeExpression:
    """Decode a jsonb-shaped dict back into a ``TypeExpression``.
    ``classes`` is a registry of name → OntologyClass for resolving
    ``ClassRef.target``."""
    kind = d["kind"]
    if kind == "primitive":
        return Primitive(d["value"])
    if kind == "array":
        return Array(of=type_from_json(d["of"], classes))
    if kind == "class_ref":
        target = d["target"]
        if target not in classes:
            raise KeyError(f"class_ref target {target!r} not loaded yet")
        return ClassRef(target=classes[target])
    raise ValueError(f"unknown type kind: {kind!r}")


# ---------------------------------------------------------------------------
# save_spec
# ---------------------------------------------------------------------------


def save_spec(
    spec: Spec,
    *,
    schema: str = "knot_meta",
) -> list[tuple[str, list[Any]]]:
    """Return parameterized INSERTs that persist ``spec`` into the
    meta-tables. The host wraps the list in a transaction and executes."""
    s = schema
    sid, sver = spec.id, spec.version
    out: list[tuple[str, list[Any]]] = []

    out.append(
        (
            f"INSERT INTO {s}.spec (id, version, content_hash) VALUES (%s, %s, %s);",
            [sid, sver, None],
        )
    )

    # Concrete + abstract classes first (mixins / slots reference them).
    for cls in spec.classes:
        if isinstance(cls, OntologyClass):
            out.append(
                (
                    f"INSERT INTO {s}.classes "
                    f"(spec_id, spec_version, name, kind, is_a, description) "
                    f"VALUES (%s, %s, %s, %s, %s, %s);",
                    [
                        sid,
                        sver,
                        cls.name,
                        cls.kind.value,
                        cls.is_a.name if cls.is_a else None,
                        cls.description,
                    ],
                )
            )
            for i, mixin in enumerate(cls.mixins):
                out.append(
                    (
                        f"INSERT INTO {s}.class_mixins "
                        f"(spec_id, spec_version, class_name, mixin_name, ordering) "
                        f"VALUES (%s, %s, %s, %s, %s);",
                        [sid, sver, cls.name, mixin.name, i],
                    )
                )
            for i, slot in enumerate(cls.slots):
                out.append(
                    (
                        f"INSERT INTO {s}.slots "
                        f"(spec_id, spec_version, class_name, name, type, "
                        f"identifier, required, description, ordering) "
                        f"VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s);",
                        [
                            sid,
                            sver,
                            cls.name,
                            slot.name,
                            json.dumps(type_to_json(slot.type)),
                            slot.identifier,
                            slot.required,
                            slot.description,
                            i,
                        ],
                    )
                )

    # Virtual classes (reference an OntologyClass via is_a). The
    # definition is an Expr — serialized to a JSON string for storage
    # in the text column.
    for cls in spec.classes:
        if isinstance(cls, VirtualClass):
            out.append(
                (
                    f"INSERT INTO {s}.virtual_classes "
                    f"(spec_id, spec_version, name, is_a, definition, description) "
                    f"VALUES (%s, %s, %s, %s, %s, %s);",
                    [
                        sid,
                        sver,
                        cls.name,
                        cls.is_a.name,
                        json.dumps(cls.definition.to_json()),
                        cls.description,
                    ],
                )
            )

    for src in spec.sources:
        out.append(
            (
                f"INSERT INTO {s}.sources "
                f"(spec_id, spec_version, name, description) "
                f"VALUES (%s, %s, %s, %s);",
                [sid, sver, src.name, src.description],
            )
        )

    for b in spec.source_bindings:
        out.append(
            (
                f"INSERT INTO {s}.source_bindings "
                f"(spec_id, spec_version, source_name, class_name, identifier_slot, "
                f"accuracy, description) "
                f"VALUES (%s, %s, %s, %s, %s, %s, %s);",
                [
                    sid,
                    sver,
                    b.source.name,
                    b.class_.name,
                    b.identifier_slot.name,
                    b.accuracy,
                    b.description,
                ],
            )
        )
        # Each mapping is a SourceMap (uses + sql). The text
        # ``sql_expression`` column carries a JSON-serialized
        # ``{"uses": [...], "sql": "..."}`` envelope so the host's
        # raw-field declarations round-trip.
        for slot_name, source_map in b.mappings.items():
            envelope = json.dumps({
                "uses": list(source_map.uses),
                "sql": source_map.sql,
            })
            out.append(
                (
                    f"INSERT INTO {s}.source_binding_mappings "
                    f"(spec_id, spec_version, source_name, class_name, slot_name, "
                    f"sql_expression) "
                    f"VALUES (%s, %s, %s, %s, %s, %s);",
                    [sid, sver, b.source.name, b.class_.name, slot_name, envelope],
                )
            )

    for c in spec.constraints:
        out.append(
            (
                f"INSERT INTO {s}.constraints "
                f"(spec_id, spec_version, name, primary_class, body, severity, message) "
                f"VALUES (%s, %s, %s, %s, %s, %s, %s);",
                [
                    sid,
                    sver,
                    c.name,
                    c.primary.name,
                    json.dumps(c.body.to_json()),
                    c.severity.value,
                    c.message,
                ],
            )
        )

    return out


# ---------------------------------------------------------------------------
# load_queries
# ---------------------------------------------------------------------------


def load_queries(
    spec_id: str,
    spec_version: str,
    *,
    schema: str = "knot_meta",
) -> dict[str, tuple[str, list[Any]]]:
    """Return ``(sql, params)`` SELECTs keyed by table name. The host
    runs each, collects rows, and feeds them to ``load_spec``."""
    s = schema
    p = [spec_id, spec_version]
    where = "WHERE spec_id = %s AND spec_version = %s"
    return {
        "spec": (
            f"SELECT * FROM {s}.spec WHERE id = %s AND version = %s;",
            list(p),
        ),
        "classes": (
            f"SELECT * FROM {s}.classes {where};",
            list(p),
        ),
        "class_mixins": (
            f"SELECT * FROM {s}.class_mixins {where} ORDER BY class_name, ordering;",
            list(p),
        ),
        "virtual_classes": (
            f"SELECT * FROM {s}.virtual_classes {where};",
            list(p),
        ),
        "slots": (
            f"SELECT * FROM {s}.slots {where} ORDER BY class_name, ordering;",
            list(p),
        ),
        "sources": (
            f"SELECT * FROM {s}.sources {where};",
            list(p),
        ),
        "source_bindings": (
            f"SELECT * FROM {s}.source_bindings {where};",
            list(p),
        ),
        "source_binding_mappings": (
            f"SELECT * FROM {s}.source_binding_mappings {where};",
            list(p),
        ),
        "constraints": (
            f"SELECT * FROM {s}.constraints {where};",
            list(p),
        ),
    }


# ---------------------------------------------------------------------------
# load_spec
# ---------------------------------------------------------------------------


def load_spec(
    rows: Mapping[str, Sequence[Mapping[str, Any]]],
) -> Spec:
    """Reassemble a ``Spec`` from rows fetched out of the meta-tables.

    ``rows`` is keyed by unqualified table name; each value is a sequence
    of column-keyed dicts. The ``spec`` key must contain exactly one row.
    """
    spec_rows = rows["spec"]
    if len(spec_rows) != 1:
        raise ValueError(f"expected 1 spec row, got {len(spec_rows)}")
    spec_row = spec_rows[0]
    spec = Spec(id=spec_row["id"], version=spec_row["version"])

    # Pass 1: instantiate every concrete + abstract class (no inheritance
    # links yet — we need the full name map before wiring them).
    classes_by_name: dict[str, OntologyClass] = {}
    class_rows = rows.get("classes", [])
    for r in class_rows:
        cls = OntologyClass(
            name=r["name"],
            kind=r["kind"],
            description=r.get("description"),
        )
        classes_by_name[r["name"]] = cls
        spec.classes.append(cls)

    # Pass 2: wire is_a + mixins.
    for r in class_rows:
        is_a_name = r.get("is_a")
        if is_a_name:
            classes_by_name[r["name"]].is_a = classes_by_name[is_a_name]
    for r in rows.get("class_mixins", []):
        classes_by_name[r["class_name"]].mixins.append(
            classes_by_name[r["mixin_name"]]
        )

    # Pass 3: slots — type may reference loaded classes, so this comes
    # after the name map is populated.
    for r in rows.get("slots", []):
        type_data = r["type"]
        if isinstance(type_data, str):
            type_data = json.loads(type_data)
        sl = Slot(
            name=r["name"],
            type=type_from_json(type_data, classes_by_name),
            identifier=bool(r.get("identifier", False)),
            required=bool(r.get("required", False)),
            description=r.get("description"),
        )
        classes_by_name[r["class_name"]].slots.append(sl)

    # Virtual classes — must come after concrete classes are populated.
    # `definition` is stored as a JSON-serialized Expr tree.
    for r in rows.get("virtual_classes", []):
        vc = VirtualClass(
            name=r["name"],
            is_a=classes_by_name[r["is_a"]],
            definition=Expr.from_json(_load_json(r["definition"])),
            description=r.get("description"),
        )
        spec.classes.append(vc)

    sources_by_name: dict[str, Source] = {}
    for r in rows.get("sources", []):
        src = Source(name=r["name"], description=r.get("description"))
        sources_by_name[r["name"]] = src
        spec.sources.append(src)

    binding_by_key: dict[tuple[str, str], SourceBinding] = {}
    for r in rows.get("source_bindings", []):
        src = sources_by_name[r["source_name"]]
        cls = classes_by_name[r["class_name"]]
        b = SourceBinding(
            source=src,
            class_=cls,
            identifier_slot=cls.get_slot(r["identifier_slot"]),
            accuracy=float(r["accuracy"]),
            description=r.get("description"),
        )
        binding_by_key[(r["source_name"], r["class_name"])] = b
        spec.source_bindings.append(b)
    # Mappings are stored as JSON envelopes {"uses": [...], "sql": "..."}
    # in the sql_expression text column. Round-trip back into SourceMap.
    for r in rows.get("source_binding_mappings", []):
        b = binding_by_key[(r["source_name"], r["class_name"])]
        env = _load_json(r["sql_expression"])
        b.mappings[r["slot_name"]] = SourceMap(
            uses=tuple(env["uses"]), sql=env["sql"]
        )

    # Constraint bodies are stored as JSON-serialized Expr trees.
    for r in rows.get("constraints", []):
        c = Constraint(
            name=r["name"],
            primary=classes_by_name[r["primary_class"]],
            body=Expr.from_json(_load_json(r["body"])),
            severity=r.get("severity", "error"),
            message=r.get("message"),
        )
        spec.constraints.append(c)

    return spec


def _load_json(value: Any) -> Any:
    """Decode a JSON value that may already be a dict (from a jsonb
    driver) or still a string (from a text column)."""
    if isinstance(value, str):
        return json.loads(value)
    return value


__all__ = [
    "type_to_json",
    "type_from_json",
    "save_spec",
    "load_queries",
    "load_spec",
]
