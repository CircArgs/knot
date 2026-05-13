"""SQL predicate validation + compilation for Constraint.body and OntologyClass.definition.

Public API
----------
parse_predicate(sql: str) -> sqlglot.Expression
    Parse a SQL predicate string (WHERE-clause fragment) against the
    postgres dialect.  Raises ``SqlPredicateError`` on syntax failure.

canonical_hash(sql: str) -> str
    Normalise via sqlglot (identify=always → quoted identifiers,
    upper-case keywords) and return sha256 hex. Two predicates that are
    byte-different but AST-equivalent produce the same hash.

referenced_columns(sql: str) -> list[str]
    Walk the parsed AST and collect every Column identifier (un-qualified
    column names only).  Does NOT validate against a spec; used by callers
    that want to inspect without a class context.

compile_to_sql(sql: str, primary_class, ctx, *, classes_by_name=None,
               outer_bindings_alias="b") -> sql.Composable
    Rewrite a SQL predicate string into an executable psycopg Composable.

    Spec-aware rewrites (when ``classes_by_name`` is provided):
      - Bare class name in FROM/JOIN: ``Credit`` → ``knot_data.credit``
        aliased ``credit__c0`` with an implicit
        ``INNER JOIN knot_data.credit_bindings credit__b0
               ON credit__b0.knot_row_id = credit__c0._knot_row_id
              AND credit__b0.valid_to IS NULL``
      - ``ClassName.slot`` column refs in the predicate rewrite to the
        deterministic alias (``credit__c0.slot``).
      - Schema-qualified references like ``knot_data.credit`` are left
        unchanged (escape hatch — author wrote DDL on purpose).
      - ``self`` (bare column) → ``<outer_bindings_alias>.canonical_id``
        (the outer row's resolved identity; default alias ``"b"``).
      - Unknown class names or slots raise ``SqlPredicateError``.

    Pre-spec-aware resolution rules:
      - Bare column name matching a stored slot on primary_class → ``s.<col>``
        (or the alias in ctx.alias).
      - Dotted name ``<a>.<b>`` where ``<a>`` resolves to a ClassRef slot on
        primary_class → emitted as an EXISTS subquery (single-hop traversal).
      - All other column references are left as-is (caller's responsibility).

    Literals embedded in the SQL string are kept as-is (sqlglot emits them
    quoted in postgres dialect). No parameter binding is performed — the
    predicate is used verbatim inside a WHERE clause, not via placeholders.

    Returns a ``psycopg.sql.SQL`` fragment.

compile_constraint_sql(constraint, cls, *, classes_by_name=None) -> (sql.Composable, list[Any])
    Full violation-select statement for compile_constraint, using the SQL
    body.  Drop-in replacement for the old ExprTree compile_constraint.
    Pass ``classes_by_name`` to enable spec-aware class-name resolution.
"""

from __future__ import annotations

from hashlib import sha256
from typing import TYPE_CHECKING, Any

import sqlglot
import sqlglot.expressions as exp
from psycopg import sql

if TYPE_CHECKING:
    from knot.spec.compile.postgres._context import CompileContext
    from knot.spec.metaschema import OntologyClass


class SqlPredicateError(Exception):
    """Raised when a SQL predicate cannot be parsed or compiled."""


# ---------------------------------------------------------------------------
# Parse
# ---------------------------------------------------------------------------


def parse_predicate(sql_str: str) -> sqlglot.Expression:
    """Parse a SQL predicate string in postgres dialect.

    Raises ``SqlPredicateError`` with a human-readable message on failure.
    """
    try:
        exprs = sqlglot.parse(sql_str, dialect="postgres")
    except Exception as exc:
        raise SqlPredicateError(f"SQL parse error: {exc}") from exc

    if not exprs or exprs[0] is None:
        raise SqlPredicateError(f"SQL predicate is empty or unparseable: {sql_str!r}")

    # sqlglot.parse returns a list; for a predicate (no SELECT/FROM) it wraps
    # the expression in a bare node.  Unwrap if it is a top-level statement.
    expr = exprs[0]
    # If it parsed as a full SELECT, reject — we only accept WHERE-clause predicates.
    if isinstance(expr, exp.Select):
        raise SqlPredicateError(
            "SQL body must be a predicate (WHERE-clause expression), not a full SELECT statement."
        )
    return expr


# ---------------------------------------------------------------------------
# Canonical hash
# ---------------------------------------------------------------------------


def canonical_hash(sql_str: str) -> str:
    """Normalise sql_str via sqlglot and return sha256 hex.

    Two byte-different but AST-equivalent predicates produce the same hash.
    """
    try:
        expr = parse_predicate(sql_str)
        normalised = expr.sql(dialect="postgres", identify=True)
    except SqlPredicateError:
        # Un-parseable: fall back to raw string hash so the diff still works.
        normalised = sql_str.strip()
    return sha256(normalised.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Column reference helpers
# ---------------------------------------------------------------------------


def referenced_columns(sql_str: str) -> list[str]:
    """Return all bare Column names referenced in the predicate."""
    try:
        expr = parse_predicate(sql_str)
    except SqlPredicateError:
        return []
    cols: list[str] = []
    for node in expr.walk():
        if isinstance(node, exp.Column):
            # Only collect the column part (not table qualifier)
            col_name = node.name
            if col_name:
                cols.append(col_name)
    return cols


# ---------------------------------------------------------------------------
# Compilation helpers
# ---------------------------------------------------------------------------


def _effective_slot_map(primary_class: OntologyClass) -> dict[str, Any]:
    """Return {slot_name: Slot} for all effective stored + derived slots."""
    from knot.spec.effective_slots import effective_slots

    return {s.name: s for s in effective_slots(primary_class)}


def _classref_slots(primary_class: OntologyClass) -> dict[str, Any]:
    """Return {slot_name: Slot} for slots whose type is a ClassRef."""
    from knot.spec.metaschema import ClassRef

    result = {}
    for slot in _effective_slot_map(primary_class).values():
        if isinstance(slot.type, ClassRef):
            result[slot.name] = slot
    return result


# ---------------------------------------------------------------------------
# Spec-aware rewrite pass
# ---------------------------------------------------------------------------


def _rewrite_spec_references(
    expr: sqlglot.Expression,
    classes_by_name: dict[str, Any],
    outer_bindings_alias: str,
    schema_name: str,
) -> str:
    """Walk the AST and apply spec-aware rewrites.

    Rewrites:
      1. Bare class names in FROM/JOIN Table nodes (no db/schema qualifier)
         that match a known class:
           ``Credit`` → table alias ``credit__c0``, plus an implicit
           INNER JOIN to ``knot_data.credit_bindings credit__b0`` for SCD2
           currency.  Author-supplied aliases are respected.
      2. Column refs whose table qualifier matches a class name (or its
         generated alias): rewrite table part to the deterministic alias.
      3. Bare ``self`` column → ``<outer_bindings_alias>.canonical_id``.

    Schema-qualified references (e.g. ``knot_data.credit``) are left
    unchanged — they are the escape hatch for raw DDL.

    Returns the rewritten SQL string (postgres dialect, ready for
    re-parsing by ``_rewrite_for_postgres``).

    Raises ``SqlPredicateError`` for unknown class names or slots.
    """
    # Map: class_name → (src_alias, bindings_alias) for each occurrence.
    # counters per class handle multiple uses of the same class.
    counter: dict[str, int] = {}
    # Map from original table alias in author SQL → (class, src_alias, bind_alias).
    # Keyed by author alias (lower-case) or class_name if no explicit alias.
    alias_map: dict[str, tuple[Any, str, str]] = {}
    # Also track class_name → src_alias for ClassName.slot rewriting (when
    # author used the class name directly as the "table" qualifier).
    class_name_to_src_alias: dict[str, str] = {}

    # Validate ClassName.slot references.
    def _validate_class_slot(class_name: str, slot_name: str) -> None:
        cls = classes_by_name[class_name]
        from knot.spec.effective_slots import effective_slots
        from knot.spec.metaschema import DefinedClass

        if isinstance(cls, DefinedClass):
            # DefinedClass inherits parent's slots.
            slots = {s.name for s in effective_slots(cls.is_a)}
        else:
            slots = {s.name for s in effective_slots(cls)}
        if slot_name not in slots:
            raise SqlPredicateError(
                f"Unknown slot '{slot_name}' on class '{class_name}'. "
                f"Known slots: {sorted(slots)}"
            )

    # Pass 1: Find all Table nodes (in FROM and JOIN clauses) whose name is a
    # known class with no schema qualifier.  Build alias_map.
    def _collect_tables(node: sqlglot.Expression) -> sqlglot.Expression:
        if not isinstance(node, exp.Table):
            return node
        # Schema-qualified → escape hatch, skip.
        if node.db or node.catalog:
            return node
        class_name = node.name
        if class_name not in classes_by_name:
            return node  # not a class reference — leave as-is

        # Determine the alias this occurrence will use.
        cnt = counter.get(class_name, 0)
        counter[class_name] = cnt + 1
        src_alias = f"{class_name.lower()}__c{cnt}"
        bind_alias = f"{class_name.lower()}__b{cnt}"

        # Author-supplied alias (if any) overrides; we still need to track
        # the generated src_alias for bindings JOIN.
        author_alias = node.alias or None
        effective_alias = author_alias if author_alias else src_alias

        # Record for pass 2 column rewriting.
        alias_map[effective_alias] = (classes_by_name[class_name], src_alias, bind_alias)
        class_name_to_src_alias[class_name] = effective_alias

        # Build the replacement table node with src_alias and a bindings JOIN.
        tbl_lower = class_name.lower()
        # New table: knot_data.<lower> AS <src_alias>
        new_tbl = exp.Table(
            this=exp.Identifier(this=tbl_lower, quoted=False),
            db=exp.Identifier(this=schema_name, quoted=False),
            alias=exp.TableAlias(this=exp.Identifier(this=src_alias, quoted=False)),
        )
        # Bindings JOIN injected as a SQL fragment — the parent FROM/Join will
        # contain this node. We use Anonymous to carry the full JOIN SQL
        # through as raw text, then reconstruct.
        # Actually: we need to inject the JOIN at the Select level, not inside
        # the Table node. Since sqlglot's transform works on individual nodes,
        # we handle it by returning a special sentinel join node.
        # Strategy: return the Table node (rewritten) and collect the bindings
        # join to inject later.
        _pending_joins.append(
            f"INNER JOIN {schema_name}.{tbl_lower}_bindings {bind_alias}"
            f" ON {bind_alias}.knot_row_id = {src_alias}._knot_row_id"
            f" AND {bind_alias}.valid_to IS NULL"
        )
        return new_tbl

    _pending_joins: list[str] = []
    expr = expr.transform(_collect_tables)

    # Pass 2: Rewrite Column nodes.
    #   a) ClassName.slot or alias.slot → src_alias.slot
    #   b) bare "self" → outer_bindings_alias.canonical_id
    def _rewrite_columns(node: sqlglot.Expression) -> sqlglot.Expression:
        if not isinstance(node, exp.Column):
            return node

        table_part = node.table  # may be "" / None
        col_part = node.name

        # Bare "self" → outer_bindings_alias.canonical_id
        if col_part == "self" and not table_part:
            return exp.Column(
                this=exp.Identifier(this="canonical_id", quoted=False),
                table=exp.Identifier(this=outer_bindings_alias, quoted=False),
            )

        if table_part:
            # Check if table_part is a class name used directly (ClassName.slot).
            if table_part in classes_by_name:
                # Validate the slot exists.
                _validate_class_slot(table_part, col_part)
                effective_alias = class_name_to_src_alias.get(table_part, table_part.lower())
                return exp.Column(
                    this=exp.Identifier(this=col_part, quoted=False),
                    table=exp.Identifier(this=effective_alias, quoted=False),
                )
            # Check if table_part is a generated alias we know about.
            if table_part in alias_map:
                return exp.Column(
                    this=exp.Identifier(this=col_part, quoted=False),
                    table=exp.Identifier(this=table_part, quoted=False),
                )
        return node

    expr = expr.transform(_rewrite_columns)

    # Pass 3: Inject pending bindings JOINs into Select nodes inside subqueries.
    # We walk the tree and for each Select that has FROM clause tables we
    # rewrote, inject the corresponding JOINs.
    if _pending_joins:
        # Parse each join string and inject it into every inner Select.
        # Simple approach: emit SQL, append JOIN fragments, re-parse.
        # This is safe because we only inject in the subquery context.
        sql_str = expr.sql(dialect="postgres")
        # Inject the pending JOINs after the first FROM clause inside the
        # innermost SELECT context. We do this by re-parsing the SQL string
        # and walking for Select nodes that contain our rewritten table names.
        # Even simpler: join injection can be done textually because the
        # aliased table names are unique and deterministic.
        # Find each generated src_alias and inject the bindings JOIN right
        # after the table reference.
        for join_sql in _pending_joins:
            # The table alias (src_alias) is the 4th word in the JOIN string:
            # "INNER JOIN schema.tbl_bindings <bind_alias> ON ..."
            # The src_alias is embedded in the ON clause as the second entity.
            # Parse the join to extract src_alias.
            # join_sql looks like:
            #   INNER JOIN knot_data.credit_bindings credit__b0 ON credit__b0.knot_row_id = credit__c0._knot_row_id AND ...
            # src_alias = credit__c0 (appears after "= " in the ON clause).
            src_alias = join_sql.split("= ")[1].split(".")[0]
            # We need to inject this JOIN after the table alias in the SQL.
            # The pattern to find is the alias followed by any whitespace then
            # WHERE or another JOIN or end-of-subquery.
            # Use string replacement: find `{src_alias}` in SQL and append JOIN after it.
            # This is correct because src_alias is unique (counter-suffixed).
            sql_str = sql_str.replace(
                f" AS {src_alias}",
                f" AS {src_alias} {join_sql}",
                1,
            )
        return sql_str

    return expr.sql(dialect="postgres")


def _rewrite_for_postgres(
    expr: sqlglot.Expression,
    primary_class: OntologyClass,
    outer_alias: str,
) -> str:
    """Walk AST and rewrite Column references to qualified aliases.

    Rules:
    - Table-qualified column ref: ``<table>.<col>`` where ``<table>`` matches
      a ClassRef slot name → rewrite as EXISTS subquery.
    - Bare column ref matching a slot on primary_class → ``<alias>.<col>``.
    - Anything else → left as-is.

    Returns the rewritten SQL string (postgres dialect).
    """
    from knot.spec.metaschema import ClassRef

    slot_map = _effective_slot_map(primary_class)
    classref_slots = _classref_slots(primary_class)

    # We do a single-pass clone + transform.
    def transform(node: sqlglot.Expression) -> sqlglot.Expression:
        if not isinstance(node, exp.Column):
            return node

        table_part = node.table  # may be "" / None for bare columns
        col_part = node.name

        if table_part:
            # Dotted: table_part.col_part
            # If table_part is a ClassRef slot name, rewrite to EXISTS subquery.
            if table_part in classref_slots:
                slot = classref_slots[table_part]
                if not isinstance(slot.type, ClassRef):
                    return node  # shouldn't happen
                target_cls = slot.type.target_class
                target_tbl = target_cls.name.lower()
                # Emit as a correlated EXISTS — we inline a SQL string here
                # because sqlglot can parse it back cleanly.
                exists_sql = (
                    f"EXISTS ("
                    f"SELECT 1 FROM knot_data.{target_tbl} _t "
                    f"JOIN knot_data.{target_tbl}_bindings _tb "
                    f"  ON _tb.knot_row_id = _t._knot_row_id AND _tb.valid_to IS NULL "
                    f"WHERE _tb.canonical_id = {outer_alias}.{table_part} "
                    f"AND _t.{col_part} IS NOT NULL"
                    f")"
                )
                # Return a raw SQL node so sqlglot passes it through verbatim.
                return exp.Anonymous(this=exists_sql, expressions=[])
            # Otherwise qualify with the outer alias if it matches a slot.
            if table_part == outer_alias or table_part in slot_map:
                # Already qualified; leave as-is but use outer_alias.
                return exp.column(col_part, table=outer_alias)
            return node

        # Bare column: qualify with outer_alias if it's a known slot.
        if col_part in slot_map:
            return exp.column(col_part, table=outer_alias)

        return node

    rewritten = expr.transform(transform)
    return rewritten.sql(dialect="postgres")


class _AnonSqlNode:
    """Wraps an already-rendered SQL string for use in psycopg sql.Composable chains."""

    def __init__(self, rendered: str) -> None:
        self._rendered = rendered

    def as_string(self, conn: Any) -> str:
        return self._rendered


def compile_to_sql(
    sql_str: str,
    primary_class: OntologyClass,
    ctx: CompileContext,
    *,
    classes_by_name: dict[str, Any] | None = None,
    outer_bindings_alias: str = "b",
) -> sql.Composable:
    """Rewrite a SQL predicate string into an executable psycopg Composable.

    Column references are resolved against primary_class's effective slots and
    qualified with ctx.alias.  ClassRef slot traversals (dot notation) are
    rewritten as correlated EXISTS subqueries.

    When ``classes_by_name`` is provided, a spec-aware first pass runs before
    the slot rewrite:
      - Bare class names in FROM/JOIN → schema-qualified with bindings JOINs.
      - ``ClassName.slot`` column refs → deterministic alias.
      - Bare ``self`` → ``<outer_bindings_alias>.canonical_id``.

    Raises ``SqlPredicateError`` on parse failure or unresolvable reference.
    """
    from knot.spec.compile.postgres._naming import schema

    expr = parse_predicate(sql_str)
    if classes_by_name:
        # Spec-aware first pass: rewrite class names and self references.
        rewritten_str = _rewrite_spec_references(
            expr,
            classes_by_name=classes_by_name,
            outer_bindings_alias=outer_bindings_alias,
            schema_name=schema(),
        )
        # Re-parse the rewritten SQL for the slot-qualification second pass.
        expr = parse_predicate(rewritten_str)
    rewritten = _rewrite_for_postgres(expr, primary_class, ctx.alias)
    return sql.SQL(rewritten)


# ---------------------------------------------------------------------------
# Full constraint SELECT statement
# ---------------------------------------------------------------------------


def compile_constraint_sql(
    constraint: Any,
    cls: OntologyClass,
    *,
    classes_by_name: dict[str, Any] | None = None,
) -> tuple[sql.Composable, list[Any]]:
    """Emit a SELECT returning offending rows in the uniform violation shape.

    Generated SQL (no parameters — literals are inlined by sqlglot):
        SELECT
            '<rule_id>' AS rule_id,
            '<class_name>' AS class_name,
            NULL::text AS slot_name,
            b.canonical_id AS offending_pk,
            row_to_json(s)::text AS detail
        FROM knot_data.<class> s
        JOIN knot_data.<class>_bindings b
            ON b.knot_row_id = s._knot_row_id AND b.valid_to IS NULL
        WHERE NOT ( <compiled body> )

    Returns ``(composable, [])`` — params list is always empty because
    literals are folded into the SQL string by sqlglot.

    Pass ``classes_by_name`` to enable spec-aware class-name and ``self``
    resolution in the constraint body.  In constraint context, the outer
    bindings alias is ``"b"`` (the bindings table for the primary class).
    """
    from knot.spec.compile.postgres._context import CompileContext
    from knot.spec.compile.postgres._naming import bindings_table_id, table_id

    ctx = CompileContext(primary_class=cls, alias="s")
    body_sql = compile_to_sql(
        constraint.body,
        cls,
        ctx,
        classes_by_name=classes_by_name,
        outer_bindings_alias="b",
    )

    stmt = sql.SQL(
        "SELECT"
        " {rule_id} AS rule_id,"
        " {class_name} AS class_name,"
        " NULL::text AS slot_name,"
        " b.canonical_id AS offending_pk,"
        " row_to_json(s)::text AS detail"
        " FROM {src_table} s"
        " JOIN {bind_table} b"
        "   ON b.knot_row_id = s._knot_row_id AND b.valid_to IS NULL"
        " WHERE NOT ({body})"
    ).format(
        rule_id=sql.Literal(constraint.name),
        class_name=sql.Literal(cls.name),
        src_table=table_id(cls),
        bind_table=bindings_table_id(cls),
        body=body_sql,
    )

    return stmt, []
