"""Spec-storage DDL — the meta-tables that persist the spec itself.

These tables are invariant: their structure does not depend on any
particular spec. A host runs ``emit_meta_ddl`` once at deploy and then
``save_spec`` / ``load_spec`` (forthcoming) round-trip ``Spec`` instances
through this schema.

Every row is keyed by ``(spec_id, spec_version)`` so multiple specs and
versions coexist without collision. Inheritance lives in two places:
``is_a`` is a single column on ``classes``; ``mixins`` is its own
many-to-many table to avoid postgres-array quirks.

Slot ``type`` lands in a ``jsonb`` column with this shape:

  - ``{"kind": "primitive", "value": "text"}``
  - ``{"kind": "array",     "of": {...}}``
  - ``{"kind": "class_ref", "target": "Movie"}``
"""

from __future__ import annotations


def emit_meta_ddl(
    *,
    schema: str = "knot_meta",
    if_not_exists: bool = False,
) -> list[str]:
    """Return the DDL that materializes the spec-storage meta-tables."""
    ct = "CREATE TABLE IF NOT EXISTS" if if_not_exists else "CREATE TABLE"
    s = schema

    stmts: list[str] = [f"CREATE SCHEMA IF NOT EXISTS {s};"]

    stmts.append(
        f"{ct} {s}.spec (\n"
        "    id text NOT NULL,\n"
        "    version text NOT NULL,\n"
        "    content_hash text,\n"
        "    PRIMARY KEY (id, version)\n"
        ");"
    )

    stmts.append(
        f"{ct} {s}.classes (\n"
        "    spec_id text NOT NULL,\n"
        "    spec_version text NOT NULL,\n"
        "    name text NOT NULL,\n"
        "    kind text NOT NULL,\n"
        "    is_a text,\n"
        "    description text,\n"
        "    PRIMARY KEY (spec_id, spec_version, name),\n"
        f"    FOREIGN KEY (spec_id, spec_version) REFERENCES {s}.spec(id, version)\n"
        ");"
    )

    stmts.append(
        f"{ct} {s}.class_mixins (\n"
        "    spec_id text NOT NULL,\n"
        "    spec_version text NOT NULL,\n"
        "    class_name text NOT NULL,\n"
        "    mixin_name text NOT NULL,\n"
        "    ordering integer NOT NULL,\n"
        "    PRIMARY KEY (spec_id, spec_version, class_name, ordering),\n"
        "    FOREIGN KEY (spec_id, spec_version, class_name)\n"
        f"        REFERENCES {s}.classes(spec_id, spec_version, name)\n"
        ");"
    )

    stmts.append(
        f"{ct} {s}.virtual_classes (\n"
        "    spec_id text NOT NULL,\n"
        "    spec_version text NOT NULL,\n"
        "    name text NOT NULL,\n"
        "    is_a text NOT NULL,\n"
        "    definition text NOT NULL,\n"
        "    description text,\n"
        "    PRIMARY KEY (spec_id, spec_version, name),\n"
        f"    FOREIGN KEY (spec_id, spec_version) REFERENCES {s}.spec(id, version)\n"
        ");"
    )

    stmts.append(
        f"{ct} {s}.slots (\n"
        "    spec_id text NOT NULL,\n"
        "    spec_version text NOT NULL,\n"
        "    class_name text NOT NULL,\n"
        "    name text NOT NULL,\n"
        "    type jsonb NOT NULL,\n"
        "    identifier boolean NOT NULL DEFAULT false,\n"
        "    required boolean NOT NULL DEFAULT false,\n"
        "    description text,\n"
        "    ordering integer NOT NULL,\n"
        "    PRIMARY KEY (spec_id, spec_version, class_name, name),\n"
        "    FOREIGN KEY (spec_id, spec_version, class_name)\n"
        f"        REFERENCES {s}.classes(spec_id, spec_version, name)\n"
        ");"
    )

    stmts.append(
        f"{ct} {s}.sources (\n"
        "    spec_id text NOT NULL,\n"
        "    spec_version text NOT NULL,\n"
        "    name text NOT NULL,\n"
        "    description text,\n"
        "    PRIMARY KEY (spec_id, spec_version, name),\n"
        f"    FOREIGN KEY (spec_id, spec_version) REFERENCES {s}.spec(id, version)\n"
        ");"
    )

    stmts.append(
        f"{ct} {s}.source_bindings (\n"
        "    spec_id text NOT NULL,\n"
        "    spec_version text NOT NULL,\n"
        "    source_name text NOT NULL,\n"
        "    class_name text NOT NULL,\n"
        "    identifier_slot text NOT NULL,\n"
        "    accuracy double precision NOT NULL DEFAULT 0.67,\n"
        "    description text,\n"
        "    PRIMARY KEY (spec_id, spec_version, source_name, class_name),\n"
        "    FOREIGN KEY (spec_id, spec_version, source_name)\n"
        f"        REFERENCES {s}.sources(spec_id, spec_version, name),\n"
        "    FOREIGN KEY (spec_id, spec_version, class_name)\n"
        f"        REFERENCES {s}.classes(spec_id, spec_version, name)\n"
        ");"
    )

    stmts.append(
        f"{ct} {s}.source_binding_mappings (\n"
        "    spec_id text NOT NULL,\n"
        "    spec_version text NOT NULL,\n"
        "    source_name text NOT NULL,\n"
        "    class_name text NOT NULL,\n"
        "    slot_name text NOT NULL,\n"
        "    sql_expression text NOT NULL,\n"
        "    PRIMARY KEY (spec_id, spec_version, source_name, class_name, slot_name),\n"
        "    FOREIGN KEY (spec_id, spec_version, source_name, class_name)\n"
        f"        REFERENCES {s}.source_bindings(spec_id, spec_version, source_name, class_name)\n"
        ");"
    )

    stmts.append(
        f"{ct} {s}.constraints (\n"
        "    spec_id text NOT NULL,\n"
        "    spec_version text NOT NULL,\n"
        "    name text NOT NULL,\n"
        "    primary_class text NOT NULL,\n"
        "    body text NOT NULL,\n"
        "    severity text NOT NULL DEFAULT 'error',\n"
        "    message text,\n"
        "    PRIMARY KEY (spec_id, spec_version, name),\n"
        "    FOREIGN KEY (spec_id, spec_version, primary_class)\n"
        f"        REFERENCES {s}.classes(spec_id, spec_version, name)\n"
        ");"
    )

    return stmts


__all__ = ["emit_meta_ddl"]
