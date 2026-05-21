package knot.ast.expr;

/**
 * Reference to a slot on a class.
 *
 * <p>Carries names (not {@code Slot} objects) to keep the {@link Expr} tree
 * independent of the {@code OntologyClass} instance. Resolution of the slot
 * name happens at compile time, where the spec is in scope.
 */
public record Ref(String className, String slotName) implements ValueExpr {

    public Ref {
        if (className == null || className.isBlank()) {
            throw new IllegalArgumentException("Ref.className must be non-blank");
        }
        if (slotName == null || slotName.isBlank()) {
            throw new IllegalArgumentException("Ref.slotName must be non-blank");
        }
    }
}
