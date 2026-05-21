package knot.ast.expr;

/** {@code <expr> BETWEEN low AND high}. */
public record Between(Expr left, Object low, Object high) implements Expr {

    public Between {
        if (left == null) {
            throw new IllegalArgumentException("Between.left must be non-null");
        }
    }
}
