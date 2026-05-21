package knot.ast.expr;

/**
 * Float-valued distance between a vector slot and a target, rendered with the
 * operator picked by the slot's {@code metric}.
 *
 * <p>Composes anywhere a float column-level expression does —
 * {@code orderBy}, {@code select}, comparison ({@code .gt(0.3)}), etc.
 * HNSW index gets used when this expression drives an {@code ORDER BY} with a
 * {@code LIMIT}.
 *
 * <p>{@code target} is either a literal vector ({@code List<Double>}) or a
 * {@link VectorRef} for cross-row distance (renders as
 * {@code col_a <op> col_b} — no {@code ::vector(N)} cast). The Python source
 * uses {@code tuple[float, ...] | VectorRef}; Java {@code Object} is the
 * narrowest type the two share without erasing structure — the pattern-match
 * in the compiler discriminates.
 */
public record VectorDistance(
        String className, String slotName, Object target, String metric, int dim)
        implements ValueExpr {

    public VectorDistance {
        if (className == null || className.isBlank()) {
            throw new IllegalArgumentException("VectorDistance.className must be non-blank");
        }
        if (slotName == null || slotName.isBlank()) {
            throw new IllegalArgumentException("VectorDistance.slotName must be non-blank");
        }
        if (target == null) {
            throw new IllegalArgumentException("VectorDistance.target must be non-null");
        }
        if (metric == null || metric.isBlank()) {
            throw new IllegalArgumentException("VectorDistance.metric must be non-blank");
        }
        if (dim <= 0) {
            throw new IllegalArgumentException("VectorDistance.dim must be positive, got " + dim);
        }
    }
}
