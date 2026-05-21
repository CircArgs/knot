package knot.ast.expr;

import java.util.Set;

/**
 * SELECT-list aggregate — {@code COUNT(*)} / {@code COUNT(col)} /
 * {@code SUM(col)} / {@code AVG(col)} / {@code MIN(col)} / {@code MAX(col)}.
 *
 * <p>A value-expression so it composes with arithmetic + comparison and can
 * land in {@code Query.select(...)} or {@code Query.orderBy(...)}. Pair with
 * {@code Query.groupBy(...)} for per-group aggregates; without {@code groupBy}
 * the query reduces to one row (scalar aggregate over the whole table).
 *
 * <p>Use the factories on {@link Expressions} ({@code count()}, {@code sum_()},
 * {@code avg()}, {@code min_()}, {@code max_()}) to build these. The
 * trailing-underscore on {@code sum_/min_/max_} in the Python source dodged
 * builtin names; the Java factories keep the same names for symmetry with
 * the source.
 *
 * <p>Chain {@link #filter(Expr)} to emit {@code AGG(...) FILTER (WHERE ...)}
 * — single-pass conditional aggregation without a correlated subquery.
 */
public record AggExpr(String kind, Expr expr, Expr filterPredicate) implements ValueExpr {

    private static final Set<String> VALID_KINDS = Set.of("count", "sum", "avg", "min", "max");

    public AggExpr {
        if (!VALID_KINDS.contains(kind)) {
            throw new IllegalArgumentException(
                    "AggExpr.kind must be one of [avg, count, max, min, sum], got '" + kind + "'");
        }
        if (expr == null && !"count".equals(kind)) {
            throw new IllegalArgumentException(
                    "AggExpr('" + kind + "') requires an inner Expr; "
                            + "only COUNT(*) is built without one");
        }
    }

    /**
     * Return a new {@code AggExpr} with {@code FILTER (WHERE predicate)}
     * appended. Single-pass conditional aggregate — avoids a correlated
     * subquery when counting subsets of the same row set.
     */
    public AggExpr filter(Expr predicate) {
        if (predicate == null) {
            throw new IllegalArgumentException("AggExpr.filter predicate must be non-null");
        }
        return new AggExpr(kind, expr, predicate);
    }
}
