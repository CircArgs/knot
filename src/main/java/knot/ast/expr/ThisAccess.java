package knot.ast.expr;

/**
 * Builder sugar — the Java equivalent of Python's magic {@code this} object.
 *
 * <p>Disambiguates nested scopes: an outer {@code person.resolved.where(...)}
 * enclosing a predicate over Movie writes {@code This.this_().cls("Person")}
 * to bind the outer row; if Movie itself was the outer scope, it'd be
 * {@code .cls("Movie")}.
 *
 * <p>Python writes {@code this.Person} via {@code __getattr__}; Java exposes
 * an explicit {@link #cls(String)} method instead.
 */
public final class ThisAccess {

    /** Singleton — the {@code this} of the Python source. */
    public static final ThisAccess INSTANCE = new ThisAccess();

    private ThisAccess() {}

    /** Bind to the outer scope's row of class {@code name}. */
    public This cls(String name) {
        return new This(name);
    }
}
