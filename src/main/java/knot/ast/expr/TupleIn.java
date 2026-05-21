package knot.ast.expr;

import java.util.List;

/**
 * Tuple-row IN-list: {@code (a, b, ...) IN ((v1, w1, ...), ...)}.
 *
 * <p>Use for batched DataLoader lookups by composite keys:
 * <pre>{@code
 * Expressions.tupleIn(
 *     List.of(movieYear, movieTitle),
 *     List.of(List.of(2020, "Tenet"), List.of(1994, "Pulp Fiction")))
 * }</pre>
 *
 * <p>All elements in {@code lefts} must be {@link Expr} nodes; {@code values} is a list of
 * same-arity value lists (any Java primitive-wrapper or {@code String}). {@code negated}
 * switches to {@code NOT IN}.
 */
public record TupleIn(List<Expr> lefts, List<List<Object>> values, boolean negated)
        implements Expr {

    public TupleIn {
        if (lefts == null || lefts.isEmpty()) {
            throw new IllegalArgumentException("TupleIn requires at least one column");
        }
        lefts = List.copyOf(lefts);
        if (values == null) {
            values = List.of();
        } else {
            for (List<?> row : values) {
                if (row == null || row.size() != lefts.size()) {
                    throw new IllegalArgumentException(
                            "TupleIn arity mismatch: "
                                    + (row == null ? "null" : row.size())
                                    + " values vs " + lefts.size() + " columns");
                }
            }
            // Deep immutable copy
            var copy = new java.util.ArrayList<List<Object>>(values.size());
            for (var row : values) {
                copy.add(List.copyOf(row));
            }
            values = List.copyOf(copy);
        }
    }
}
