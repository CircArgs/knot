package knot.ast.select;

import knot.ast.expr.Expr;

/**
 * One ORDER BY clause — a ref expression and a direction.
 *
 * <p>Mirrors the Python {@code OrderBy} frozen dataclass. Direction is validated at
 * construction time; only {@code "asc"} and {@code "desc"} are accepted.
 */
public record OrderBy(Expr ref, String direction) {

    public OrderBy {
        if (ref == null) {
            throw new IllegalArgumentException("OrderBy.ref must be non-null");
        }
        if (!"asc".equals(direction) && !"desc".equals(direction)) {
            throw new IllegalArgumentException(
                    "OrderBy direction must be 'asc' or 'desc', got '" + direction + "'");
        }
    }

    /** Convenience ctor — defaults direction to {@code "asc"}, matching the Python default. */
    public OrderBy(Expr ref) {
        this(ref, "asc");
    }
}
