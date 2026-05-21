package knot.ast.expr;

/**
 * Outer-scope binding reference.
 *
 * <p>{@code This.this_().cls("Person")} renders as the canonical_id of the row
 * currently being filtered in the enclosing query. Only valid inside an
 * {@link Aggregate} predicate; {@code className} must match the enclosing
 * scope's class (compile-time check).
 *
 * <h2>Deviation from Python</h2>
 *
 * <p>Python uses a magic {@code this} object with {@code __getattr__}, written
 * as {@code this.Person}. Java has no equivalent — the static factory
 * {@link #this_()} returns a {@link ThisAccess} builder and
 * {@link ThisAccess#cls(String)} produces the {@link This} node.
 */
public record This(String className) implements ValueExpr {

    public This {
        if (className == null || className.isBlank()) {
            throw new IllegalArgumentException("This.className must be non-blank");
        }
    }

    /** Entry point — {@code This.this_().cls("Person")}. */
    public static ThisAccess this_() {
        return ThisAccess.INSTANCE;
    }
}
