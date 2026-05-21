package knot.ast.expr;

/**
 * {@code (SELECT COUNT(*) FROM other WHERE other.fk = primary.identifier
 * [AND extra-where])}.
 *
 * <p>A value-expression — produced by
 * {@code (other.col().get("fk").eq(this.cls("Primary"))).count()} and composes
 * with comparison operators: {@code … .count().ge(1)}.
 */
public record CountRel(
        String otherClassName,
        String fkSlotName,
        String primaryClassName,
        String primaryIdentifier,
        Expr where) implements ValueExpr {

    public CountRel {
        if (otherClassName == null || otherClassName.isBlank()) {
            throw new IllegalArgumentException("CountRel.otherClassName must be non-blank");
        }
        if (fkSlotName == null || fkSlotName.isBlank()) {
            throw new IllegalArgumentException("CountRel.fkSlotName must be non-blank");
        }
        if (primaryClassName == null || primaryClassName.isBlank()) {
            throw new IllegalArgumentException("CountRel.primaryClassName must be non-blank");
        }
        if (primaryIdentifier == null || primaryIdentifier.isBlank()) {
            throw new IllegalArgumentException("CountRel.primaryIdentifier must be non-blank");
        }
        // where may be null (no extra predicate)
    }
}
