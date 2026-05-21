package knot.ast.expr;

/** {@code <expr> IS NULL} (or {@code IS NOT NULL} if {@code negated}). */
public record IsNull(Expr expr, boolean negated) implements Expr {

    public IsNull {
        if (expr == null) {
            throw new IllegalArgumentException("IsNull.expr must be non-null");
        }
    }
}
