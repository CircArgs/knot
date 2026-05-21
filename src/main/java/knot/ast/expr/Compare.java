package knot.ast.expr;

/** Binary comparison: {@code left <op> right} (=, &lt;&gt;, &lt;, &lt;=, &gt;, &gt;=). */
public record Compare(String op, Expr left, Expr right) implements Expr {

    public Compare {
        if (op == null || op.isBlank()) {
            throw new IllegalArgumentException("Compare.op must be non-blank");
        }
        if (left == null) {
            throw new IllegalArgumentException("Compare.left must be non-null");
        }
        if (right == null) {
            throw new IllegalArgumentException("Compare.right must be non-null");
        }
    }
}
