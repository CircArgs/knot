package knot.ast.expr;

/**
 * Boolean predicate — the FK column's value matches a canonical_id on the
 * target class's resolved (or other) view.
 *
 * <p>Produced by {@link FkRef#targetExists()}. Renders as:
 * <pre>
 *   [NOT] EXISTS (
 *     SELECT 1 FROM &lt;schema&gt;.&lt;target_class&gt;&lt;layer&gt;
 *     WHERE &lt;schema&gt;.&lt;target_class&gt;&lt;layer&gt;.&lt;targetIdentifierSlot&gt;
 *         = &lt;schema&gt;.&lt;fk_class&gt;&lt;layer&gt;.&lt;fkSlotName&gt;
 *   )
 * </pre>
 *
 * <p>{@code targetIdentifierSlot} defaults to {@code "canonical_id"} — the
 * universal identifier slot name in knot. The {@code negated} flag flips
 * {@code EXISTS} to {@code NOT EXISTS}; combine with the boolean combinators
 * on {@link Expr} for richer predicates.
 */
public record TargetExists(
        String fkClassName,
        String fkSlotName,
        String targetClassName,
        String targetIdentifierSlot,
        boolean negated) implements Expr {

    public TargetExists {
        if (fkClassName == null || fkClassName.isBlank()) {
            throw new IllegalArgumentException("TargetExists.fkClassName must be non-blank");
        }
        if (fkSlotName == null || fkSlotName.isBlank()) {
            throw new IllegalArgumentException("TargetExists.fkSlotName must be non-blank");
        }
        if (targetClassName == null || targetClassName.isBlank()) {
            throw new IllegalArgumentException("TargetExists.targetClassName must be non-blank");
        }
        if (targetIdentifierSlot == null || targetIdentifierSlot.isBlank()) {
            throw new IllegalArgumentException(
                    "TargetExists.targetIdentifierSlot must be non-blank");
        }
    }

    /** Return a copy with {@code negated} flipped. */
    public TargetExists negate() {
        return new TargetExists(
                fkClassName, fkSlotName, targetClassName, targetIdentifierSlot, !negated);
    }
}
