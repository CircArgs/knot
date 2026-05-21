package knot.ast.expr;

/**
 * {@code [NOT] EXISTS (SELECT 1 FROM other WHERE other.fk = primary.identifier
 * [AND extra-where])}.
 *
 * <p>Produced by the correlated-aggregate form
 * {@code (other.col().get("fk").eq(this.cls("Primary"))).any_() / .none_()} —
 * {@code negated=true} yields {@code NOT EXISTS}.
 */
public record Exists(
        String otherClassName,
        String fkSlotName,
        String primaryClassName,
        String primaryIdentifier,
        Expr where,
        boolean negated) implements Expr {

    public Exists {
        if (otherClassName == null || otherClassName.isBlank()) {
            throw new IllegalArgumentException("Exists.otherClassName must be non-blank");
        }
        if (fkSlotName == null || fkSlotName.isBlank()) {
            throw new IllegalArgumentException("Exists.fkSlotName must be non-blank");
        }
        if (primaryClassName == null || primaryClassName.isBlank()) {
            throw new IllegalArgumentException("Exists.primaryClassName must be non-blank");
        }
        if (primaryIdentifier == null || primaryIdentifier.isBlank()) {
            throw new IllegalArgumentException("Exists.primaryIdentifier must be non-blank");
        }
        // where may be null (no extra predicate)
    }
}
