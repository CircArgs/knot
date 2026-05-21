package knot.ast.expr;

import java.util.ArrayList;
import java.util.List;

/**
 * Static factory class for all module-level expression builders.
 *
 * <p>Mirrors the Python module-level functions in {@code knot.ast.expr}:
 * {@code count()}, {@code sum_()}, {@code avg()}, {@code min_()}, {@code max_()},
 * {@code lit()}, {@code raw()}, {@code tuple_lt/le/gt/ge()}, {@code tuple_in()},
 * {@code tuple_not_in()}, and the {@code this_()} / {@code ThisAccess} sugar.
 *
 * <p>Import statically for the most concise call site:
 * <pre>{@code
 * import static knot.ast.expr.Expressions.*;
 * }</pre>
 */
public final class Expressions {

    private Expressions() {}

    // ------------------------------------------------------------------
    // Internal coercion helper (Python: _as_expr)
    // ------------------------------------------------------------------

    /**
     * Coerce a value into an {@link Expr}. If {@code v} is already an {@code Expr},
     * it is returned as-is; otherwise it is wrapped in a {@link Literal}.
     * Mirrors Python {@code _as_expr}.
     */
    public static Expr asExpr(Object v) {
        if (v instanceof Expr e) return e;
        return new Literal(v);
    }

    // ------------------------------------------------------------------
    // AggExpr factories
    // ------------------------------------------------------------------

    /** {@code COUNT(*)} — no argument form. */
    public static AggExpr count() {
        return new AggExpr("count", null, null);
    }

    /** {@code COUNT(<expr>)} — counts non-NULL values. */
    public static AggExpr count(Expr expr) {
        return new AggExpr("count", expr, null);
    }

    /** {@code SUM(<expr>)} over the (grouped or full) row set. */
    public static AggExpr sum_(Expr expr) {
        return new AggExpr("sum", expr, null);
    }

    /** {@code AVG(<expr>)} — postgres returns {@code numeric} for integer input. */
    public static AggExpr avg(Expr expr) {
        return new AggExpr("avg", expr, null);
    }

    /** {@code MIN(<expr>)}. */
    public static AggExpr min_(Expr expr) {
        return new AggExpr("min", expr, null);
    }

    /** {@code MAX(<expr>)}. */
    public static AggExpr max_(Expr expr) {
        return new AggExpr("max", expr, null);
    }

    // ------------------------------------------------------------------
    // Literal / Raw
    // ------------------------------------------------------------------

    /** Wrap a Java value as an {@link Expr} literal. */
    public static Literal lit(Object value) {
        return new Literal(value);
    }

    /** Escape-hatch SQL fragment. Treats {@code sql} as opaque postgres. */
    public static Raw raw(String sql) {
        return new Raw(sql);
    }

    // ------------------------------------------------------------------
    // TupleCompare factories (keyset pagination)
    // ------------------------------------------------------------------

    /**
     * {@code (a, b) < (v1, v2)} — keyset pagination "before" cursor.
     *
     * <p>{@code rights} elements are coerced via {@link #asExpr(Object)}.
     */
    public static TupleCompare tupleLt(List<Expr> lefts, List<?> rights) {
        return new TupleCompare("<", lefts, coerceRights(rights));
    }

    /** {@code (a, b) <= (v1, v2)}. */
    public static TupleCompare tupleLe(List<Expr> lefts, List<?> rights) {
        return new TupleCompare("<=", lefts, coerceRights(rights));
    }

    /** {@code (a, b) > (v1, v2)} — keyset pagination "after" cursor. */
    public static TupleCompare tupleGt(List<Expr> lefts, List<?> rights) {
        return new TupleCompare(">", lefts, coerceRights(rights));
    }

    /** {@code (a, b) >= (v1, v2)}. */
    public static TupleCompare tupleGe(List<Expr> lefts, List<?> rights) {
        return new TupleCompare(">=", lefts, coerceRights(rights));
    }

    // ------------------------------------------------------------------
    // TupleIn factories
    // ------------------------------------------------------------------

    /**
     * {@code (a, b) IN ((v1, w1), (v2, w2))} — batched composite-key lookup.
     *
     * @param lefts  column expressions (must be non-empty)
     * @param values list of same-arity value rows
     */
    public static TupleIn tupleIn(List<Expr> lefts, List<List<Object>> values) {
        return new TupleIn(lefts, values, false);
    }

    /** {@code (a, b) NOT IN ((v1, w1), (v2, w2))}. */
    public static TupleIn tupleNotIn(List<Expr> lefts, List<List<Object>> values) {
        return new TupleIn(lefts, values, true);
    }

    // ------------------------------------------------------------------
    // this_ / ThisAccess
    // ------------------------------------------------------------------

    /**
     * Builder sugar — {@code this_().get("Person")} returns {@code new This("Person")}.
     *
     * <p>Mirrors the Python {@code this} magic accessor object. Java has no
     * {@code __getattr__} equivalent; use {@link ThisAccess#get(String)} explicitly.
     */
    public static ThisAccess this_() {
        return new ThisAccess();
    }

    /**
     * Builder object returned by {@link #this_()} — {@code this_().get("Person")}
     * returns {@code new This("Person")}.
     */
    public static final class ThisAccess {
        private ThisAccess() {}

        /** Return a {@link This} node bound to the named outer-scope class. */
        public This get(String className) {
            if (className == null || className.isBlank()) {
                throw new IllegalArgumentException(
                        "ThisAccess.get className must be non-blank");
            }
            return new This(className);
        }
    }

    // ------------------------------------------------------------------
    // Internal helpers
    // ------------------------------------------------------------------

    private static List<Expr> coerceRights(List<?> rights) {
        var result = new ArrayList<Expr>(rights.size());
        for (var v : rights) {
            result.add(asExpr(v));
        }
        return result;
    }
}
