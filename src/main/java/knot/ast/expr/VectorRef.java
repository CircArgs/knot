package knot.ast.expr;

import java.util.List;

/**
 * Reference to a Vector slot.
 *
 * <p>Adds {@link #distanceTo(Object)} so k-NN sort / filter / select composes
 * with every other query builder. Carries the slot's {@code metric} +
 * {@code dim} so the compiler picks the right pgvector operator without a spec
 * lookup.
 */
public record VectorRef(String className, String slotName, String metric, int dim)
        implements ValueExpr {

    public VectorRef {
        if (className == null || className.isBlank()) {
            throw new IllegalArgumentException("VectorRef.className must be non-blank");
        }
        if (slotName == null || slotName.isBlank()) {
            throw new IllegalArgumentException("VectorRef.slotName must be non-blank");
        }
        if (metric == null || metric.isBlank()) {
            throw new IllegalArgumentException("VectorRef.metric must be non-blank");
        }
        if (dim <= 0) {
            throw new IllegalArgumentException("VectorRef.dim must be a positive integer, got " + dim);
        }
    }

    /**
     * Float-valued {@link VectorDistance} expression between this slot and
     * {@code target}. The operator is picked from the slot's declared
     * {@code metric} — {@code <=>} for cosine, {@code <->} for l2,
     * {@code <#>} for ip — matching the slot's HNSW index, so
     * {@code orderBy(slot.distanceTo(v)).limit(k)} uses the index.
     *
     * <p>{@code target} may be:
     * <ul>
     *   <li>a {@code List<? extends Number>} literal vector (renders with
     *       {@code ::vector(N)} cast), OR</li>
     *   <li>another {@link VectorRef} for cross-row distance (renders as
     *       {@code col_a <op> col_b} — no cast, both sides are typed columns).
     *       Cross-row refs must share the same {@code metric} and {@code dim}.</li>
     * </ul>
     */
    public VectorDistance distanceTo(Object target) {
        if (target instanceof VectorRef other) {
            if (!other.metric.equals(metric)) {
                throw new IllegalArgumentException(
                        "distanceTo: metric mismatch ('" + metric
                                + "' vs '" + other.metric + "')");
            }
            if (other.dim != dim) {
                throw new IllegalArgumentException(
                        "distanceTo: dim mismatch (" + dim + " vs " + other.dim + ")");
            }
            return new VectorDistance(className, slotName, other, metric, dim);
        }
        if (target instanceof List<?> raw) {
            var copy = new java.util.ArrayList<Double>(raw.size());
            for (var v : raw) {
                if (v instanceof Number n) {
                    copy.add(n.doubleValue());
                } else {
                    throw new IllegalArgumentException(
                            "distanceTo: literal vector elements must be numeric, got "
                                    + (v == null ? "null" : v.getClass().getName()));
                }
            }
            return new VectorDistance(className, slotName, List.copyOf(copy), metric, dim);
        }
        throw new IllegalArgumentException(
                "distanceTo: target must be a VectorRef or List<? extends Number>, got "
                        + (target == null ? "null" : target.getClass().getName()));
    }
}
