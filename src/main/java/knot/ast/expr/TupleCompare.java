package knot.ast.expr;

import java.util.List;
import java.util.Set;

/**
 * Tuple-row comparison — {@code (a, b, ...) <op> (v1, v2, ...)}.
 *
 * <p>Postgres semantically lexicographic. The canonical keyset-pagination shape:
 * {@code Expressions.tupleLt(List.of(year, canonicalId), List.of(cursorY, cursorId))}.
 * All elements on the left side must be {@link Expr} nodes (typically {@link Ref} or
 * {@link FkRef}); right-side values are coerced to {@link Literal} via
 * {@link Expressions#asExpr(Object)}.
 *
 * <p>Only lex-order operators ({@code <}, {@code <=}, {@code >}, {@code >=}) are
 * accepted — equality tuple comparison is expressed as multiple {@link Compare} nodes.
 */
public record TupleCompare(String op, List<Expr> lefts, List<Expr> rights) implements Expr {

    private static final Set<String> VALID_OPS = Set.of("<", "<=", ">", ">=");

    public TupleCompare {
        if (!VALID_OPS.contains(op)) {
            throw new IllegalArgumentException(
                    "TupleCompare.op must be a lex-order comparator (<, <=, >, >=), got '"
                            + op + "'");
        }
        if (lefts == null || lefts.isEmpty()) {
            throw new IllegalArgumentException("TupleCompare requires at least one column");
        }
        if (rights == null || lefts.size() != rights.size()) {
            throw new IllegalArgumentException(
                    "TupleCompare arity mismatch: "
                            + lefts.size() + " left vs "
                            + (rights == null ? 0 : rights.size()) + " right");
        }
        lefts = List.copyOf(lefts);
        rights = List.copyOf(rights);
    }
}
