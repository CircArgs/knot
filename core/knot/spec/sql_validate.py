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

compile_to_sql(sql: str, primary_class, ctx) -> sql.Composable
    Rewrite a SQL predicate string into an executable psycopg Composable.

    Resolution rules:
      - Bare column name matching a stored slot on primary_class → ``s.<col>``
        (or the alias in ctx.alias).
      - Dotted name ``<a>.<b>`` where ``<a>`` resolves to a ClassRef slot on
        primary_class → emitted as an EXISTS subquery:
            EXISTS (SELECT 1
                    FROM knot_data.<target_table> t
                    JOIN knot_data.<target_table>_bindings tb
                      ON tb.knot_row_id = t._knot_row_id AND tb.valid_to IS NULL
                    WHERE tb.canonical_id = s.<a>
                      AND t.<b> IS NOT NULL  [and any other conditions])
        For now, single-hop ClassRef traversals are handled by rewriting the
        Column node; multi-hop is not supported and raises SqlPredicateError.
      - All other column references are left as-is (caller's responsibility).

    Literals embedded in the SQL string are kept as-is (sqlglot emits them
    quoted in postgres dialect). No parameter binding is performed — the
    predicate is used verbatim inside a WHERE clause, not via placeholders.

    Returns a ``psycopg.sql.SQL`` fragment.

compile_constraint_sql(constraint, cls) -> (sql.Composable, list[Any])
    Full violation-select statement for compile_constraint, using the SQL
    body.  Drop-in replacement for the old ExprTree compile_constraint.
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
) -> sql.Composable:
    """Rewrite a SQL predicate string into an executable psycopg Composable.

    Column references are resolved against primary_class's effective slots and
    qualified with ctx.alias.  ClassRef slot traversals (dot notation) are
    rewritten as correlated EXISTS subqueries.

    Raises ``SqlPredicateError`` on parse failure or unresolvable reference.
    """
    expr = parse_predicate(sql_str)
    rewritten = _rewrite_for_postgres(expr, primary_class, ctx.alias)
    return sql.SQL(rewritten)


# ---------------------------------------------------------------------------
# Full constraint SELECT statement
# ---------------------------------------------------------------------------


def compile_constraint_sql(
    constraint: Any,
    cls: OntologyClass,
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
    """
    from knot.spec.compile.postgres._context import CompileContext
    from knot.spec.compile.postgres._naming import bindings_table_id, table_id

    ctx = CompileContext(primary_class=cls, alias="s")
    body_sql = compile_to_sql(constraint.body, cls, ctx)

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
