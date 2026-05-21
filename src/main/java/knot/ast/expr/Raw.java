package knot.ast.expr;

/**
 * Escape-hatch SQL fragment.
 *
 * <p>The user owns its correctness; knot does not parse, validate, or rewrite
 * it. Use sparingly for postgres-specific constructs the builder doesn't model
 * (window funcs, CTEs, JSON ops, custom functions). Deliberately not a
 * {@link ValueExpr} — raw SQL has no inferable type so it cannot participate in
 * the typed comparison helpers; wrap in {@link Expressions#raw(String)} only as
 * a last resort.
 */
public record Raw(String sql) implements Expr {

    public Raw {
        if (sql == null) {
            throw new IllegalArgumentException("Raw.sql must be non-null");
        }
    }
}
