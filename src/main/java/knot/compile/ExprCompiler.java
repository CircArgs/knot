package knot.compile;

import java.util.List;
import java.util.Map;
import java.util.stream.Collectors;

import knot.ast.expr.AggExpr;
import knot.ast.expr.Aggregate;
import knot.ast.expr.Between;
import knot.ast.expr.BoolOp;
import knot.ast.expr.Compare;
import knot.ast.expr.CountRel;
import knot.ast.expr.Exists;
import knot.ast.expr.Expr;
import knot.ast.expr.FkChainRef;
import knot.ast.expr.FkRef;
import knot.ast.expr.InList;
import knot.ast.expr.IsNull;
import knot.ast.expr.Literal;
import knot.ast.expr.Not;
import knot.ast.expr.Raw;
import knot.ast.expr.Ref;
import knot.ast.expr.TargetExists;
import knot.ast.expr.This;
import knot.ast.expr.TupleCompare;
import knot.ast.expr.TupleIn;
import knot.ast.expr.VectorDistance;
import knot.ast.expr.VectorRef;
import knot.ast.select.Layer;

/**
 * Postgres SQL compilation for the {@link Expr} tree.
 *
 * <p>Pattern-matching switch over the sealed {@link Expr} hierarchy replaces the Python
 * {@code @functools.singledispatch} table. One case per AST node type. Adding a second
 * compilation target (Trino / Spark / cypher) is a new class with its own switch — the
 * {@code Expr} records don't change.
 *
 * <p>{@code layer} flips the rendered table reference between the canonical table, the resolved
 * view, the bindings table, and the per-source provenance view — the same {@code Expr} tree
 * compiles against any of them.
 *
 * <p>{@code outerClass} carries the enclosing query's class name through recursive compilation so
 * that {@link This} references can render against the outer row. Pass {@code null} for top-level
 * compilation; set by {@link Aggregate} for its sub-predicate and by {@link QueryCompiler} for the
 * top-level WHERE clause of a query that contains aggregates.
 *
 * <p>Ports {@code knot/compile/expr.py}.
 */
public final class ExprCompiler {

    private ExprCompiler() {}

    // pgvector distance operators, one per metric. Each matches the operator class used by knot's
    // HNSW index emission, so an ORDER BY slot <op> target LIMIT k plan uses the index.
    private static final Map<String, String> VECTOR_DISTANCE_OP = Map.of(
            "cosine", "<=>",
            "l2", "<->",
            "ip", "<#>");

    private static final Map<String, String> AGG_FN = Map.of(
            "count", "COUNT",
            "sum", "SUM",
            "avg", "AVG",
            "min", "MIN",
            "max", "MAX");

    // ------------------------------------------------------------------
    // Public entry points
    // ------------------------------------------------------------------

    /**
     * Render {@code node} as a postgres SQL fragment. {@code outerClass} defaults to {@code null}
     * (top-level compilation).
     */
    public static String compileSql(Expr node, String schema, Layer layer) {
        return compileSql(node, schema, layer, null);
    }

    /**
     * Render {@code node} as a postgres SQL fragment.
     *
     * @param node       the expression to compile
     * @param schema     the postgres schema name
     * @param layer      which relation variant to target
     * @param outerClass enclosing query's class name (for {@link This} refs); {@code null} at
     *                   top level
     */
    public static String compileSql(Expr node, String schema, Layer layer, String outerClass) {
        return switch (node) {
            case Ref r ->
                    "%s.%s%s.%s".formatted(schema, r.className().toLowerCase(), layer.suffix(), r.slotName());

            case FkRef f ->
                    "%s.%s%s.%s".formatted(schema, f.className().toLowerCase(), layer.suffix(), f.slotName());

            case VectorRef v ->
                    "%s.%s%s.%s".formatted(schema, v.className().toLowerCase(), layer.suffix(), v.slotName());

            case VectorDistance d -> compileVectorDistance(d, schema, layer, outerClass);

            case FkChainRef c ->
                    "%s.%s".formatted(Aliases.chainAlias(c.sourceClass(), c.chain()), c.terminalSlot());

            case Literal lit -> sqlLiteral(lit.value());

            case This t -> compileThis(t, schema, layer, outerClass);

            case Compare cmp -> {
                var lhs = compileSql(cmp.left(), schema, layer, outerClass);
                var rhs = compileSql(cmp.right(), schema, layer, outerClass);
                yield "%s %s %s".formatted(lhs, cmp.op(), rhs);
            }

            case BoolOp b -> {
                var lhs = compileSql(b.left(), schema, layer, outerClass);
                var rhs = compileSql(b.right(), schema, layer, outerClass);
                yield "(%s) %s (%s)".formatted(lhs, b.op(), rhs);
            }

            case Not n ->
                    "NOT (%s)".formatted(compileSql(n.expr(), schema, layer, outerClass));

            case IsNull isnull -> {
                var inner = compileSql(isnull.expr(), schema, layer, outerClass);
                var op = isnull.negated() ? "IS NOT NULL" : "IS NULL";
                yield "%s %s".formatted(inner, op);
            }

            case InList inlist -> {
                var lhs = compileSql(inlist.left(), schema, layer, outerClass);
                var vs = inlist.values().stream()
                        .map(ExprCompiler::sqlLiteral)
                        .collect(Collectors.joining(", "));
                var op = inlist.negated() ? "NOT IN" : "IN";
                yield "%s %s (%s)".formatted(lhs, op, vs);
            }

            case Between b -> {
                var lhs = compileSql(b.left(), schema, layer, outerClass);
                yield "%s BETWEEN %s AND %s".formatted(lhs, sqlLiteral(b.low()), sqlLiteral(b.high()));
            }

            case Exists e -> compileExists(e, schema, layer, outerClass);

            case TargetExists te -> compileTargetExists(te, schema, layer);

            case CountRel cr -> compileCountRel(cr, schema, layer, outerClass);

            case Aggregate agg -> compileAggregate(agg, schema, layer, outerClass);

            case AggExpr ae -> compileAggExpr(ae, schema, layer, outerClass);

            case TupleCompare tc -> compileTupleCompare(tc, schema, layer, outerClass);

            case TupleIn ti -> compileTupleIn(ti, schema, layer, outerClass);

            case Raw raw -> raw.sql();
        };
    }

    // ------------------------------------------------------------------
    // Private per-node helpers
    // ------------------------------------------------------------------

    private static String compileVectorDistance(
            VectorDistance d, String schema, Layer layer, String outerClass) {
        var op = VECTOR_DISTANCE_OP.get(d.metric());
        if (op == null) {
            throw new IllegalArgumentException(
                    "Unknown vector metric '" + d.metric() + "'; expected cosine, l2, or ip");
        }
        var col = "%s.%s%s.%s".formatted(schema, d.className().toLowerCase(), layer.suffix(), d.slotName());
        if (d.target() instanceof VectorRef other) {
            // Cross-row distance: both sides are typed columns — no cast needed.
            var otherCol = compileSql(other, schema, layer, outerClass);
            return "(%s %s %s)".formatted(col, op, otherCol);
        }
        // Literal vector — pgvector's text form: '[0.1, 0.2, ...]'.
        // Query.sql() inlines all literals — there's no parameter list.
        @SuppressWarnings("unchecked")
        var nums = (List<Double>) d.target();
        var literal = "[" + nums.stream()
                .map(v -> {
                    // Match Python repr(float(v)): use plain double toString
                    // but ensure at least one decimal (repr(1.0) = "1.0")
                    String s = Double.toString(v);
                    return s;
                })
                .collect(Collectors.joining(", ")) + "]";
        return "(%s %s '%s'::vector(%d))".formatted(col, op, literal, d.dim());
    }

    private static String compileThis(This t, String schema, Layer layer, String outerClass) {
        if (outerClass == null) {
            throw new IllegalArgumentException(
                    "this." + t.className() + " used outside of an Aggregate context");
        }
        if (!t.className().equals(outerClass)) {
            throw new IllegalArgumentException(
                    "this." + t.className() + " doesn't match the enclosing class ('"
                            + outerClass + "') — outer-scope reference mismatched");
        }
        // The outer row's identity column. knot convention: canonical_id.
        return "%s.%s%s.canonical_id".formatted(schema, outerClass.toLowerCase(), layer.suffix());
    }

    private static String compileExists(Exists e, String schema, Layer layer, String outerClass) {
        var otherTable = "%s.%s%s".formatted(schema, e.otherClassName().toLowerCase(), layer.suffix());
        var primaryTable = "%s.%s%s".formatted(schema, e.primaryClassName().toLowerCase(), layer.suffix());
        var clauses = new java.util.ArrayList<String>();
        clauses.add("%s.%s = %s.%s".formatted(
                otherTable, e.fkSlotName(), primaryTable, e.primaryIdentifier()));
        if (e.where() != null) {
            clauses.add(compileSql(e.where(), schema, layer, outerClass));
        }
        var prefix = e.negated() ? "NOT EXISTS" : "EXISTS";
        return "%s (SELECT 1 FROM %s WHERE %s)".formatted(
                prefix, otherTable, String.join(" AND ", clauses));
    }

    private static String compileTargetExists(TargetExists te, String schema, Layer layer) {
        var targetTable = "%s.%s%s".formatted(schema, te.targetClassName().toLowerCase(), layer.suffix());
        var fkTable = "%s.%s%s".formatted(schema, te.fkClassName().toLowerCase(), layer.suffix());
        var prefix = te.negated() ? "NOT EXISTS" : "EXISTS";
        return "%s (SELECT 1 FROM %s WHERE %s.%s = %s.%s)".formatted(
                prefix, targetTable,
                targetTable, te.targetIdentifierSlot(),
                fkTable, te.fkSlotName());
    }

    private static String compileCountRel(
            CountRel cr, String schema, Layer layer, String outerClass) {
        var otherTable = "%s.%s%s".formatted(schema, cr.otherClassName().toLowerCase(), layer.suffix());
        var primaryTable = "%s.%s%s".formatted(schema, cr.primaryClassName().toLowerCase(), layer.suffix());
        var clauses = new java.util.ArrayList<String>();
        clauses.add("%s.%s = %s.%s".formatted(
                otherTable, cr.fkSlotName(), primaryTable, cr.primaryIdentifier()));
        if (cr.where() != null) {
            clauses.add(compileSql(cr.where(), schema, layer, outerClass));
        }
        return "(SELECT COUNT(*) FROM %s WHERE %s)".formatted(
                otherTable, String.join(" AND ", clauses));
    }

    private static String compileAggregate(
            Aggregate agg, String schema, Layer layer, String outerClass) {
        // Infer the primary class — the class whose slot refs appear in the predicate
        // (ignoring This refs, which point to outer scope).
        var primary = inferPrimaryClass(agg.predicate());
        if (primary == null) {
            throw new IllegalArgumentException(
                    "Aggregate.predicate has no class-bound slot refs; cannot "
                            + "infer the primary row-set. Predicate: " + agg.predicate());
        }
        var subTable = "%s.%s%s".formatted(schema, primary.toLowerCase(), layer.suffix());
        // The aggregate's sub-predicate compiles with outerClass unchanged (it propagates
        // the enclosing scope, since This refs in the predicate bind to the enclosing query,
        // not the subquery itself).
        var predSql = compileSql(agg.predicate(), schema, layer, outerClass);

        return switch (agg.kind()) {
            case "any" -> "EXISTS (SELECT 1 FROM %s WHERE %s)".formatted(subTable, predSql);
            case "none" -> "NOT EXISTS (SELECT 1 FROM %s WHERE %s)".formatted(subTable, predSql);
            case "count" -> "(SELECT COUNT(*) FROM %s WHERE %s)".formatted(subTable, predSql);
            case "all" -> {
                // Universal as "no counter-example": NOT EXISTS (… AND NOT cond).
                // condition is required for kind="all" (Aggregate constructor enforces this).
                var condSql = compileSql(agg.condition(), schema, layer, outerClass);
                yield "NOT EXISTS (SELECT 1 FROM %s WHERE %s AND NOT (%s))".formatted(
                        subTable, predSql, condSql);
            }
            default -> throw new IllegalStateException("unknown Aggregate.kind: " + agg.kind());
        };
    }

    private static String compileAggExpr(
            AggExpr ae, String schema, Layer layer, String outerClass) {
        var fn = AGG_FN.get(ae.kind());
        if (fn == null) {
            throw new IllegalArgumentException("Unknown AggExpr.kind: " + ae.kind());
        }
        String agg;
        if (ae.expr() == null) {
            // Only valid for COUNT — AggExpr constructor already enforced.
            agg = fn + "(*)";
        } else {
            var inner = compileSql(ae.expr(), schema, layer, outerClass);
            agg = fn + "(" + inner + ")";
        }
        if (ae.filterPredicate() != null) {
            var pred = compileSql(ae.filterPredicate(), schema, layer, outerClass);
            return agg + " FILTER (WHERE " + pred + ")";
        }
        return agg;
    }

    private static String compileTupleCompare(
            TupleCompare tc, String schema, Layer layer, String outerClass) {
        var lhs = "("
                + tc.lefts().stream()
                        .map(e -> compileSql(e, schema, layer, outerClass))
                        .collect(Collectors.joining(", "))
                + ")";
        var rhs = "("
                + tc.rights().stream()
                        .map(e -> compileSql(e, schema, layer, outerClass))
                        .collect(Collectors.joining(", "))
                + ")";
        return "%s %s %s".formatted(lhs, tc.op(), rhs);
    }

    private static String compileTupleIn(
            TupleIn ti, String schema, Layer layer, String outerClass) {
        var lhs = "("
                + ti.lefts().stream()
                        .map(e -> compileSql(e, schema, layer, outerClass))
                        .collect(Collectors.joining(", "))
                + ")";
        var vals = ti.values().stream()
                .map(row -> "(" + row.stream()
                        .map(ExprCompiler::sqlLiteral)
                        .collect(Collectors.joining(", ")) + ")")
                .collect(Collectors.joining(", "));
        var op = ti.negated() ? "NOT IN" : "IN";
        return "%s %s (%s)".formatted(lhs, op, vals);
    }

    // ------------------------------------------------------------------
    // Package-private helpers used by QueryCompiler
    // ------------------------------------------------------------------

    /**
     * Walk {@code node} and return the class name of the first non-{@link This} slot reference
     * encountered. Used by {@link Aggregate} to pick its sub-query's FROM table.
     *
     * <p>Returns {@code null} if no class-bound ref is reachable (an aggregate over only
     * {@code this}/literals — nonsensical, but caller surfaces a clear error).
     */
    static String inferPrimaryClass(Expr node) {
        return switch (node) {
            case This ignored -> null;
            case Ref r -> r.className();
            case FkRef f -> f.className();
            case VectorRef v -> v.className();
            case FkChainRef c -> c.sourceClass();
            case Compare cmp -> {
                var left = inferPrimaryClass(cmp.left());
                yield left != null ? left : inferPrimaryClass(cmp.right());
            }
            case BoolOp b -> {
                var left = inferPrimaryClass(b.left());
                yield left != null ? left : inferPrimaryClass(b.right());
            }
            case Not n -> inferPrimaryClass(n.expr());
            case IsNull isnull -> inferPrimaryClass(isnull.expr());
            case InList inlist -> inferPrimaryClass(inlist.left());
            case Between b -> inferPrimaryClass(b.left());
            case Aggregate agg ->
                    // Nested aggregate — peek into its predicate for inference at this level.
                    inferPrimaryClass(agg.predicate());
            default -> null;
        };
    }

    /**
     * Postgres SQL literal serialization for primitive values.
     *
     * <p>Handles {@code null → NULL}, booleans, strings (single-quote escaped), numbers,
     * and lists ({@code ARRAY[...]}).
     */
    static String sqlLiteral(Object v) {
        if (v == null) return "NULL";
        if (v instanceof Boolean b) return b ? "TRUE" : "FALSE";
        if (v instanceof String s) return "'" + s.replace("'", "''") + "'";
        if (v instanceof Integer || v instanceof Long) return v.toString();
        if (v instanceof Double d) return d.toString();
        if (v instanceof Float f) return f.toString();
        if (v instanceof Number n) return n.toString();
        if (v instanceof List<?> list) {
            var inner = list.stream()
                    .map(ExprCompiler::sqlLiteral)
                    .collect(Collectors.joining(", "));
            return "ARRAY[" + inner + "]";
        }
        throw new IllegalArgumentException(
                "can't serialize " + v.getClass().getSimpleName() + " as SQL literal: " + v);
    }
}
