package knot.ast.expr;

import java.util.List;

/**
 * Reference to a FK slot.
 *
 * <p>Used as a value, renders as the FK column on the source table (e.g.,
 * {@code movie.col().get("director").eq(X)}). Used as a navigator,
 * {@link #walk(String)} returns an {@link FkChainRef} representing the joined
 * ref into the target class.
 *
 * <p>{@code targetClassName} is carried so navigation knows where to point the
 * chain. Validation of the target slot's existence is deferred to compile time
 * (this record stays pure data, no spec access).
 *
 * <h2>Deviation from Python</h2>
 *
 * <p>Python uses {@code __getattr__} so {@code movie.col.director.name}
 * walks transparently. Java has no equivalent — chain navigation is exposed
 * via explicit {@link #walk(String)} (one-hop terminal) and
 * {@link #targetExists()} (referential-integrity predicate). For multi-hop
 * chains use {@link FkChainRef#walk(String, String, String)}.
 */
public record FkRef(String className, String slotName, String targetClassName)
        implements ValueExpr {

    public FkRef {
        if (className == null || className.isBlank()) {
            throw new IllegalArgumentException("FkRef.className must be non-blank");
        }
        if (slotName == null || slotName.isBlank()) {
            throw new IllegalArgumentException("FkRef.slotName must be non-blank");
        }
        if (targetClassName == null || targetClassName.isBlank()) {
            throw new IllegalArgumentException("FkRef.targetClassName must be non-blank");
        }
    }

    /**
     * Walk one FK hop and land on a terminal slot of the target class —
     * the Java equivalent of Python's {@code movie.col.director.name}.
     *
     * @param terminalSlot slot name on the target class to project
     */
    public FkChainRef walk(String terminalSlot) {
        return new FkChainRef(
                className,
                List.of(new FkChainRef.Hop(slotName, targetClassName)),
                terminalSlot);
    }

    /**
     * Boolean predicate: this FK column's value matches some
     * {@code canonical_id} (or {@code targetIdentifierSlot}) in the target
     * class's resolved view. Defaults to {@code "canonical_id"} — the universal
     * identifier slot name in knot.
     */
    public TargetExists targetExists() {
        return new TargetExists(
                className, slotName, targetClassName, "canonical_id", false);
    }

    /**
     * Boolean predicate against an explicitly-named identifier slot on the
     * target class. Use the no-arg overload unless your spec uses a
     * non-standard identifier name.
     */
    public TargetExists targetExists(String targetIdentifierSlot) {
        return new TargetExists(
                className, slotName, targetClassName, targetIdentifierSlot, false);
    }
}
