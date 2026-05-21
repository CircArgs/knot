package knot.ast.expr;

import java.util.List;

/**
 * {@code <expr> IN (...)} (or {@code NOT IN} if {@code negated}).
 *
 * <p>Python's source uses {@code tuple[Any, ...]}; in Java the values are an
 * immutable {@code List<Object>}. Construction copies via
 * {@link List#copyOf(java.util.Collection)} to defend against later
 * mutation by the caller.
 */
public record InList(Expr left, List<Object> values, boolean negated) implements Expr {

    public InList {
        if (left == null) {
            throw new IllegalArgumentException("InList.left must be non-null");
        }
        if (values == null) {
            throw new IllegalArgumentException("InList.values must be non-null");
        }
        // Defensive copy — protects against caller-side mutation after
        // construction. List.copyOf gives an immutable snapshot.
        values = List.copyOf(values);
    }
}
