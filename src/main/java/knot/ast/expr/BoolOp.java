package knot.ast.expr;

/**
 * Boolean AND/OR of two {@link Expr}s.
 *
 * <p>Use {@link Expr#not_()} for NOT (returns {@link Not}).
 */
public record BoolOp(String op, Expr left, Expr right) implements Expr {

    public BoolOp {
        if (!"AND".equals(op) && !"OR".equals(op)) {
            throw new IllegalArgumentException(
                    "BoolOp.op must be 'AND' or 'OR', got '" + op + "'");
        }
        if (left == null) {
            throw new IllegalArgumentException("BoolOp.left must be non-null");
        }
        if (right == null) {
            throw new IllegalArgumentException("BoolOp.right must be non-null");
        }
    }
}
