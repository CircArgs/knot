"""Diff driver — walks the spec, compares against the live DB via the
introspection helpers, returns ordered ``MigrationOp`` records.

Phases: rename pre-pass → drops → adds. ``allow_destructive=False``
(the default) filters out DROP TABLE / DROP COLUMN at the end; other
drops (DROP VIEW, DROP INDEX, DELETE FROM weight) are always emitted.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from knot.ast.types import ClassRef
from knot.compile.ddl import (
    _emit_bindings_indexes,
    _emit_bindings_table,
    _emit_fk_alters,
    _emit_table,
    _emit_view,
    _emit_weight_table,
)
from knot.compile.migrate._introspect import (
    QueryFn,
    _ColInfo,
    _existing_column_details,
    _existing_columns,
    _existing_fk_constraints,
    _existing_indexes,
    _existing_schemas,
    _existing_tables,
    _existing_views,
    _existing_weight_rows,
)
from knot.compile.resolver import emit_all_sources_view, emit_resolved_view
from knot.spec import OntologyClass, Spec


@dataclass(frozen=True, slots=True)
class MigrationOp:
    """One migration operation.

    Attributes
    ----------
    description
        Short human-readable summary suitable for a Flyway filename.
    sql
        The SQL the host runs to apply the change. Always rendered with
        ``IF NOT EXISTS`` style idempotency where postgres supports it.
    destructive
        True when applying the op may discard data. Phase 1 emits no
        destructive ops; the flag is reserved for Phase 2.
    target
        Coarse classification — ``"schema"``, ``"weight_table"``,
        ``"canonical"``, ``"bindings"``, ``"index"``, ``"fk"``,
        ``"resolved_view"``, ``"all_sources_view"``, ``"virtual_view"``,
        ``"weight_seed"``. Useful for grouping ops in migration files.
    """

    description: str
    sql: str
    destructive: bool = False
    target: str = ""


def diff_against_db(
    spec: Spec,
    query: QueryFn,
    *,
    schema: str = "knot_data",
    bindings_suffix: str = "_bindings",
    resolved_suffix: str = "_resolved",
    all_sources_suffix: str = "_all_sources",
    weight_table_name: str = "source_weight",
    allow_destructive: bool = False,
    renames: dict[str, dict[str, str]] | None = None,
) -> list[MigrationOp]:
    """Walk the spec, compare against the live database via ``query``,
    return ordered migration operations.

    Two passes:

      A. **Drops** (Phase 2). Things in the database that aren't in the
         spec. Non-destructive cleanup (DROP FK / VIEW / INDEX, DELETE
         FROM weight) is always emitted. Truly destructive ops (DROP
         TABLE, DROP COLUMN) carry ``destructive=True`` and are
         filtered out unless ``allow_destructive=True``.

      B. **Adds** (Phase 1). CREATE / ALTER ADD / CREATE OR REPLACE /
         UPSERT to bring the database into alignment with the spec.

    Ordering: drops run first (cleaning the old state) so any
    subsequent adds don't collide with stale objects.

    Drop sequence: FK constraints → views → indexes → weight rows →
    columns → tables. Add sequence: schema → weight table → canonical
    tables → bindings tables (+ indexes) → ALTER ADD columns → ALTER
    ADD FK constraints → resolved views → virtual views → weight seed.

    ``renames`` (optional) declares explicit ``{class_name: {old_col:
    new_col}}`` mappings. Renames run BEFORE drops or adds so the rest
    of the diff sees the post-rename column layout. Renames are emitted
    as ``ALTER TABLE … RENAME COLUMN`` for both the canonical table and
    its bindings table; non-destructive.
    """
    renames = renames or {}
    ops: list[MigrationOp] = []

    # Rename pre-pass — emit renames first so subsequent passes match
    # what the database will look like after they run.
    ops.extend(
        _diff_renames(
            renames,
            query,
            schema=schema,
            bindings_suffix=bindings_suffix,
        )
    )

    ops.extend(
        _diff_drops(
            spec,
            query,
            schema=schema,
            bindings_suffix=bindings_suffix,
            resolved_suffix=resolved_suffix,
            all_sources_suffix=all_sources_suffix,
            weight_table_name=weight_table_name,
            renames=renames,
        )
    )

    # 1. Schema
    if schema not in _existing_schemas(query):
        ops.append(
            MigrationOp(
                description=f"create_schema_{schema}",
                sql=f"CREATE SCHEMA IF NOT EXISTS {schema};",
                target="schema",
            )
        )
    db_tables = _existing_tables(query, schema)

    # 2. Weight table
    if weight_table_name not in db_tables:
        ops.append(
            MigrationOp(
                description=f"create_table_{weight_table_name}",
                sql=_emit_weight_table(
                    schema=schema,
                    weight_table_name=weight_table_name,
                    if_not_exists=True,
                ),
                target="weight_table",
            )
        )

    # 3-6. Per-class structure
    for cls in spec.concrete_classes():
        ops.extend(
            _diff_concrete_class(
                cls,
                query=query,
                db_tables=db_tables,
                schema=schema,
                bindings_suffix=bindings_suffix,
                renames=renames.get(cls.name, {}),
            )
        )

    # 7. Resolved views (always replace — handles spec changes too)
    for cls in spec.concrete_classes():
        view_name = f"{cls.name.lower()}{resolved_suffix}"
        sql = emit_resolved_view(
            spec,
            cls,
            schema=schema,
            bindings_suffix=bindings_suffix,
            resolved_suffix=resolved_suffix,
            weight_table_name=weight_table_name,
            if_not_exists=True,
        )
        ops.append(
            MigrationOp(
                description=f"replace_view_{view_name}",
                sql=sql,
                target="resolved_view",
            )
        )

    # 7b. All-sources / provenance views (always replace, same shape).
    for cls in spec.concrete_classes():
        view_name = f"{cls.name.lower()}{all_sources_suffix}"
        sql = emit_all_sources_view(
            spec,
            cls,
            schema=schema,
            bindings_suffix=bindings_suffix,
            all_sources_suffix=all_sources_suffix,
            weight_table_name=weight_table_name,
            if_not_exists=True,
        )
        ops.append(
            MigrationOp(
                description=f"replace_view_{view_name}",
                sql=sql,
                target="all_sources_view",
            )
        )

    # 8. Virtual class views
    for vc in spec.virtual_classes():
        sql = _emit_view(vc, schema=schema, if_not_exists=True)
        ops.append(
            MigrationOp(
                description=f"replace_view_{vc.name.lower()}",
                sql=sql,
                target="virtual_view",
            )
        )

    # 9. Weight seed — INSERT-only. Spec values are *initial conditions*;
    # once a (source, class, slot) row exists, the operator's runtime
    # tuning is authoritative and we do NOT clobber it on redeploy.
    # Emit INSERT only for net-new (source, class, slot) triples.
    weight_rows = _existing_weight_rows(query, schema, weight_table_name)
    for b in spec.source_bindings:
        cls = b.class_
        ident_name = b.identifier_slot.name
        for slot in cls.effective_slots():
            if slot.name == ident_name:
                continue
            key = (b.source.name, cls.name, slot.name)
            if key in weight_rows:
                continue  # row exists — leave operator's tuning alone
            source_lit = "'" + b.source.name.replace("'", "''") + "'"
            class_lit = "'" + cls.name.replace("'", "''") + "'"
            slot_lit = "'" + slot.name.replace("'", "''") + "'"
            weight = b.weight_for(slot.name)
            ops.append(
                MigrationOp(
                    description=(f"seed_weight_{b.source.name}_{cls.name}_{slot.name}"),
                    sql=(
                        f"INSERT INTO {schema}.{weight_table_name} "
                        f"(source_name, class_name, slot_name, weight) "
                        f"VALUES ({source_lit}, {class_lit}, {slot_lit}, {weight})\n"
                        f"ON CONFLICT (source_name, class_name, slot_name) DO NOTHING;"
                    ),
                    target="weight_seed",
                )
            )

    if not allow_destructive:
        ops = [op for op in ops if not op.destructive]
    return ops


def _diff_renames(
    renames: dict[str, dict[str, str]],
    query: QueryFn,
    *,
    schema: str,
    bindings_suffix: str,
) -> list[MigrationOp]:
    """Emit ``ALTER TABLE … RENAME COLUMN`` for each declared rename.

    Renames are non-destructive; postgres just relabels the column.
    We emit on both the canonical table and its bindings table when
    each exists in the database — silently skipping when either is
    missing so a partially-built schema still works."""
    if not renames:
        return []
    db_tables = _existing_tables(query, schema)
    ops: list[MigrationOp] = []
    for class_name, col_map in renames.items():
        canonical = class_name.lower()
        bindings = f"{canonical}{bindings_suffix}"
        for old, new in col_map.items():
            if canonical in db_tables:
                existing = _existing_columns(query, schema, canonical)
                if old in existing and new not in existing:
                    ops.append(
                        MigrationOp(
                            description=(f"rename_column_{canonical}_{old}_to_{new}"),
                            sql=(
                                f"ALTER TABLE {schema}.{canonical} RENAME COLUMN {old} TO {new};"
                            ),
                            target="canonical",
                        )
                    )
            if bindings in db_tables:
                existing = _existing_columns(query, schema, bindings)
                if old in existing and new not in existing:
                    ops.append(
                        MigrationOp(
                            description=(f"rename_column_{bindings}_{old}_to_{new}"),
                            sql=(
                                f"ALTER TABLE {schema}.{bindings} RENAME COLUMN {old} TO {new};"
                            ),
                            target="bindings",
                        )
                    )
    return ops


def _apply_rename_translation(
    cols: Any,
    rename_map: dict[str, str],
) -> Any:
    """Translate column names from the pre-rename DB shape to the
    post-rename target shape so the rest of the diff sees a consistent
    world. Works for either dict (column details) or set (names only)."""
    if isinstance(cols, dict):
        return {rename_map.get(name, name): info for name, info in cols.items()}
    return {rename_map.get(c, c) for c in cols}


def _diff_drops(
    spec: Spec,
    query: QueryFn,
    *,
    schema: str,
    bindings_suffix: str,
    resolved_suffix: str,
    all_sources_suffix: str,
    weight_table_name: str,
    renames: dict[str, dict[str, str]] | None = None,
) -> list[MigrationOp]:
    """Detect everything that's in the database but no longer matches
    the spec, and emit DROP / DELETE ops.

    Ordering: FK constraints → views → indexes → weight rows → columns →
    tables. Within each tier, non-destructive ops first so a partial
    apply leaves the DB in a usable state.
    """
    ops: list[MigrationOp] = []
    db_tables = _existing_tables(query, schema)
    db_views = _existing_views(query, schema)

    expected_canonical = {cls.name.lower() for cls in spec.concrete_classes()}
    expected_bindings = {f"{n}{bindings_suffix}" for n in expected_canonical}
    expected_resolved_views = {f"{n}{resolved_suffix}" for n in expected_canonical}
    expected_all_sources_views = {
        f"{n}{all_sources_suffix}" for n in expected_canonical
    }
    expected_virtual_views = {cls.name.lower() for cls in spec.virtual_classes()}
    expected_views = (
        expected_resolved_views | expected_all_sources_views | expected_virtual_views
    )

    # 1. Unused FK constraints on canonical tables that ARE still in spec.
    for cls in spec.concrete_classes():
        canonical_name = cls.name.lower()
        if canonical_name not in db_tables:
            continue
        expected_fks = {
            f"fk_{canonical_name}_{slot.name}"
            for slot in cls.effective_slots()
            if isinstance(slot.type, ClassRef)
        }
        for fk in sorted(
            _existing_fk_constraints(query, schema, canonical_name) - expected_fks
        ):
            ops.append(
                MigrationOp(
                    description=f"drop_fk_{fk}",
                    sql=(
                        f"ALTER TABLE {schema}.{canonical_name} DROP CONSTRAINT IF EXISTS {fk};"
                    ),
                    target="fk",
                )
            )

    def _view_target(view: str) -> str:
        if view.endswith(all_sources_suffix):
            return "all_sources_view"
        if view.endswith(resolved_suffix):
            return "resolved_view"
        return "virtual_view"

    # 2a. Unused views — drop views that aren't in spec at all.
    for view in sorted(db_views - expected_views):
        ops.append(
            MigrationOp(
                description=f"drop_view_{view}",
                sql=f"DROP VIEW IF EXISTS {schema}.{view};",
                target=_view_target(view),
            )
        )

    # 2b. Drop currently-existing resolved + all-sources + virtual views
    # even if they ARE expected, so subsequent column drops / renames /
    # type changes on the underlying tables don't blow up with
    # "DependentObjectsStillExist". The additive pass recreates them
    # via CREATE OR REPLACE VIEW. Cheap, always safe.
    for view in sorted(db_views & expected_views):
        ops.append(
            MigrationOp(
                description=f"drop_view_{view}_for_rebuild",
                sql=f"DROP VIEW IF EXISTS {schema}.{view};",
                target=_view_target(view),
            )
        )

    # 3. Unused indexes on bindings tables that ARE still in spec.
    for cls in spec.concrete_classes():
        bindings_name = f"{cls.name.lower()}{bindings_suffix}"
        if bindings_name not in db_tables:
            continue
        expected_idxs = {
            f"{bindings_name}_current_idx",
            f"{bindings_name}_source_idx",
        }
        for idx in sorted(_existing_indexes(query, schema, bindings_name)):
            if idx in expected_idxs:
                continue
            # postgres-auto PK index; leave alone.
            if idx.endswith("_pkey"):
                continue
            ops.append(
                MigrationOp(
                    description=f"drop_index_{idx}",
                    sql=f"DROP INDEX IF EXISTS {schema}.{idx};",
                    target="index",
                )
            )

    # 4. Unused weight rows (source/class/slot triples no longer in spec).
    expected_triples: set[tuple[str, str, str]] = set()
    for b in spec.source_bindings:
        cls = b.class_
        ident_name = b.identifier_slot.name
        for slot in cls.effective_slots():
            if slot.name == ident_name:
                continue
            expected_triples.add((b.source.name, cls.name, slot.name))
    db_weight_rows = _existing_weight_rows(query, schema, weight_table_name)
    for src, cls_name, slot_name in sorted(set(db_weight_rows) - expected_triples):
        s_lit = "'" + src.replace("'", "''") + "'"
        c_lit = "'" + cls_name.replace("'", "''") + "'"
        sl_lit = "'" + slot_name.replace("'", "''") + "'"
        ops.append(
            MigrationOp(
                description=f"drop_weight_{src}_{cls_name}_{slot_name}",
                sql=(
                    f"DELETE FROM {schema}.{weight_table_name} "
                    f"WHERE source_name = {s_lit} "
                    f"AND class_name = {c_lit} "
                    f"AND slot_name = {sl_lit};"
                ),
                target="weight_seed",
            )
        )

    # 5. Unused columns (destructive). Renamed columns are excluded
    # from drop candidates — the rename pre-pass already relabeled them.
    renames = renames or {}
    bindings_framework_cols = {
        "source_name",
        "source_identifier",
        "raw_payload",
        "er_metadata",
        "valid_from",
        "valid_to",
    }
    for cls in spec.concrete_classes():
        canonical_name = cls.name.lower()
        bindings_name = f"{canonical_name}{bindings_suffix}"
        slot_cols = {slot.name for slot in cls.effective_slots()}
        class_renames = renames.get(cls.name, {})
        if canonical_name in db_tables:
            existing = _apply_rename_translation(
                _existing_columns(query, schema, canonical_name),
                class_renames,
            )
            for col in sorted(existing - slot_cols):
                ops.append(
                    MigrationOp(
                        description=f"drop_column_{canonical_name}_{col}",
                        sql=(
                            f"ALTER TABLE {schema}.{canonical_name} DROP COLUMN IF EXISTS {col};"
                        ),
                        destructive=True,
                        target="canonical",
                    )
                )
        if bindings_name in db_tables:
            existing = _apply_rename_translation(
                _existing_columns(query, schema, bindings_name),
                class_renames,
            )
            for col in sorted(existing - slot_cols - bindings_framework_cols):
                ops.append(
                    MigrationOp(
                        description=f"drop_column_{bindings_name}_{col}",
                        sql=(
                            f"ALTER TABLE {schema}.{bindings_name} DROP COLUMN IF EXISTS {col};"
                        ),
                        destructive=True,
                        target="bindings",
                    )
                )

    # 6. Unused tables (destructive). Classes no longer in spec take
    # their canonical + bindings tables with them.
    expected_data_tables = expected_canonical | expected_bindings | {weight_table_name}
    for table in sorted(db_tables - expected_data_tables):
        target = "bindings" if table.endswith(bindings_suffix) else "canonical"
        ops.append(
            MigrationOp(
                description=f"drop_table_{table}",
                sql=f"DROP TABLE IF EXISTS {schema}.{table} CASCADE;",
                destructive=True,
                target=target,
            )
        )

    return ops


def _diff_concrete_class(
    cls: OntologyClass,
    *,
    query: QueryFn,
    db_tables: set[str],
    schema: str,
    bindings_suffix: str,
    renames: dict[str, str] | None = None,
) -> list[MigrationOp]:
    renames = renames or {}
    ops: list[MigrationOp] = []
    canonical_name = cls.name.lower()
    bindings_name = f"{canonical_name}{bindings_suffix}"

    # Canonical table
    if canonical_name not in db_tables:
        ops.append(
            MigrationOp(
                description=f"create_table_{canonical_name}",
                sql=_emit_table(cls, schema=schema, if_not_exists=True),
                target="canonical",
            )
        )
    else:
        existing_details = _apply_rename_translation(
            _existing_column_details(query, schema, canonical_name),
            renames,
        )
        for slot in cls.effective_slots():
            if slot.name not in existing_details:
                ops.append(
                    MigrationOp(
                        description=(f"add_column_{canonical_name}_{slot.name}"),
                        sql=_add_column_sql(schema, canonical_name, slot),
                        target="canonical",
                    )
                )
            else:
                ops.extend(
                    _diff_column_type_and_nullability(
                        schema=schema,
                        table=canonical_name,
                        slot=slot,
                        existing=existing_details[slot.name],
                        expected_nullable=not (slot.identifier or slot.required),
                    )
                )

    # FK constraints on canonical (always emitted IF NOT EXISTS via the
    # ALTER's drop-and-add pair; idempotent at the DDL level).
    existing_fks = (
        _existing_fk_constraints(query, schema, canonical_name)
        if canonical_name in db_tables
        else set()
    )
    for fk_sql in _emit_fk_alters(cls, schema=schema, if_not_exists=True):
        # Parse out the constraint name from the ALTER's ADD line.
        constraint = _extract_fk_name(fk_sql)
        if constraint is not None and constraint in existing_fks:
            # Already on the table — skip both DROP and ADD lines for it.
            if "DROP CONSTRAINT" in fk_sql:
                continue
            # Otherwise this is the ADD line for an existing constraint —
            # skip; the if_not_exists=True drop is in a separate stmt.
            continue
        # Skip the DROP IF EXISTS line for constraints we know don't
        # exist (cleaner; postgres tolerates them but the SQL is noise).
        if "DROP CONSTRAINT IF EXISTS" in fk_sql and constraint not in existing_fks:
            continue
        ops.append(
            MigrationOp(
                description=f"add_fk_{constraint or 'unnamed'}",
                sql=fk_sql,
                target="fk",
            )
        )

    # Bindings table
    if bindings_name not in db_tables:
        ops.append(
            MigrationOp(
                description=f"create_table_{bindings_name}",
                sql=_emit_bindings_table(
                    cls,
                    schema=schema,
                    bindings_suffix=bindings_suffix,
                    if_not_exists=True,
                ),
                target="bindings",
            )
        )
        for idx_sql in _emit_bindings_indexes(
            cls,
            schema=schema,
            bindings_suffix=bindings_suffix,
            if_not_exists=True,
        ):
            ops.append(
                MigrationOp(
                    description=(f"create_index_{_extract_index_name(idx_sql)}"),
                    sql=idx_sql,
                    target="index",
                )
            )
    else:
        existing_bdetails = _apply_rename_translation(
            _existing_column_details(query, schema, bindings_name),
            renames,
        )
        for slot in cls.effective_slots():
            if slot.name not in existing_bdetails:
                ops.append(
                    MigrationOp(
                        description=(f"add_column_{bindings_name}_{slot.name}"),
                        # Bindings columns are nullable (partial claims).
                        sql=_add_column_sql(
                            schema, bindings_name, slot, force_nullable=True
                        ),
                        target="bindings",
                    )
                )
            else:
                # Identifier is NOT NULL on bindings; everything else
                # is nullable (partial claims).
                expected_nullable = not slot.identifier
                ops.extend(
                    _diff_column_type_and_nullability(
                        schema=schema,
                        table=bindings_name,
                        slot=slot,
                        existing=existing_bdetails[slot.name],
                        expected_nullable=expected_nullable,
                    )
                )
        # Ensure raw_payload + er_metadata framework columns exist.
        if "raw_payload" not in existing_bdetails:
            ops.append(
                MigrationOp(
                    description=f"add_column_{bindings_name}_raw_payload",
                    sql=(
                        f"ALTER TABLE {schema}.{bindings_name}\n"
                        "    ADD COLUMN IF NOT EXISTS raw_payload "
                        "jsonb NOT NULL DEFAULT '{}'::jsonb;"
                    ),
                    target="bindings",
                )
            )
        if "er_metadata" not in existing_bdetails:
            ops.append(
                MigrationOp(
                    description=f"add_column_{bindings_name}_er_metadata",
                    sql=(
                        f"ALTER TABLE {schema}.{bindings_name}\n"
                        "    ADD COLUMN IF NOT EXISTS er_metadata "
                        "jsonb NOT NULL DEFAULT '{}'::jsonb;"
                    ),
                    target="bindings",
                )
            )
        # Indexes — emit any that aren't present.
        existing_idxs = _existing_indexes(query, schema, bindings_name)
        for idx_sql in _emit_bindings_indexes(
            cls,
            schema=schema,
            bindings_suffix=bindings_suffix,
            if_not_exists=True,
        ):
            idx_name = _extract_index_name(idx_sql)
            if idx_name is not None and idx_name in existing_idxs:
                continue
            ops.append(
                MigrationOp(
                    description=f"create_index_{idx_name or 'unnamed'}",
                    sql=idx_sql,
                    target="index",
                )
            )

    return ops


# ---------------------------------------------------------------------------
# Column-level helpers
# ---------------------------------------------------------------------------


def _diff_column_type_and_nullability(
    *,
    schema: str,
    table: str,
    slot: Any,
    existing: _ColInfo,
    expected_nullable: bool,
) -> list[MigrationOp]:
    """Emit ALTER COLUMN ops when an existing column's type or nullability
    doesn't match the spec.

    Type mismatch → ``ALTER COLUMN … TYPE … USING <column>::<new_type>``
    (destructive — the cast may fail or lose precision; host should
    review the USING expression).

    Nullability mismatch:
      - existing nullable + spec wants NOT NULL → ``SET NOT NULL``
        (destructive — fails if any row currently has NULL).
      - existing NOT NULL + spec wants nullable → ``DROP NOT NULL``
        (always safe; just widens the set of permitted values).
    """
    from knot.compile.ddl import _pg_type

    ops: list[MigrationOp] = []
    expected_type = _pg_type(slot.type)
    if existing.pg_type != expected_type:
        ops.append(
            MigrationOp(
                description=f"alter_column_type_{table}_{slot.name}",
                sql=(
                    f"ALTER TABLE {schema}.{table}\n"
                    f"    ALTER COLUMN {slot.name} TYPE {expected_type}\n"
                    f"    USING {slot.name}::{expected_type};"
                ),
                destructive=True,
                target="canonical" if not table.endswith("_bindings") else "bindings",
            )
        )

    if existing.nullable and not expected_nullable:
        ops.append(
            MigrationOp(
                description=f"set_not_null_{table}_{slot.name}",
                sql=(
                    f"ALTER TABLE {schema}.{table}\n    ALTER COLUMN {slot.name} SET NOT NULL;"
                ),
                destructive=True,
                target="canonical" if not table.endswith("_bindings") else "bindings",
            )
        )
    elif (not existing.nullable) and expected_nullable:
        ops.append(
            MigrationOp(
                description=f"drop_not_null_{table}_{slot.name}",
                sql=(
                    f"ALTER TABLE {schema}.{table}\n    ALTER COLUMN {slot.name} DROP NOT NULL;"
                ),
                target="canonical" if not table.endswith("_bindings") else "bindings",
            )
        )
    return ops


def _add_column_sql(
    schema: str,
    table: str,
    slot: Any,
    *,
    force_nullable: bool = False,
) -> str:
    """``ALTER TABLE … ADD COLUMN IF NOT EXISTS … type [NOT NULL DEFAULT
    …]`` for one slot. ``force_nullable`` overrides the slot's
    ``identifier`` / ``required`` flags (used on the bindings table,
    where every non-identifier slot is nullable to allow partial
    claims)."""
    from knot.compile.ddl import _pg_type

    column = f"{slot.name} {_pg_type(slot.type)}"
    not_null = not force_nullable and (slot.identifier or slot.required)
    if not_null:
        # Adding a NOT NULL column to a table with existing rows requires
        # a default. Use the postgres-default for the type if the user
        # didn't specify one; the host can edit the migration to
        # backfill before applying if they have existing rows.
        column += " NOT NULL DEFAULT ''"  # safe default; only relevant for text
    return f"ALTER TABLE {schema}.{table}\n    ADD COLUMN IF NOT EXISTS {column};"


def _extract_fk_name(fk_sql: str) -> str | None:
    """Pull ``fk_<class>_<slot>`` out of an ALTER TABLE statement
    emitted by _emit_fk_alters. Returns None on shape mismatch."""
    if "ADD CONSTRAINT " in fk_sql:
        after = fk_sql.split("ADD CONSTRAINT ", 1)[1]
        return after.split()[0].strip()
    if "DROP CONSTRAINT IF EXISTS " in fk_sql:
        after = fk_sql.split("DROP CONSTRAINT IF EXISTS ", 1)[1]
        return after.split(";", 1)[0].strip()
    return None


def _extract_index_name(idx_sql: str) -> str | None:
    """Pull the index name from a CREATE INDEX statement."""
    head = idx_sql.split("(", 1)[0]
    head = head.replace("IF NOT EXISTS", "")
    parts = head.replace("CREATE INDEX", "").strip().split()
    return parts[0] if parts else None
