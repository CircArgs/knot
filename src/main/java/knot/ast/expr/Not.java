package knot.ast.expr;

/** Boolean negation of an {@link Expr}. */
public record Not(Expr expr) implements Expr {

    public Not {
        if (expr == null) {
            throw new IllegalArgumentException("Not.expr must be non-null");
        }
    }
}
