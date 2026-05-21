package knot.ast.types;

import java.util.Map;

/**
 * Dense embedding column backed by pgvector.
 *
 * <p>{@code dim} is the embedding dimension (must be positive); {@code metric} picks
 * both the operator family for the HNSW index and the comparison operator at query
 * time. The closed set of supported metrics is {@code cosine} / {@code l2} / {@code ip}.
 */
public record Vector(int dim, String metric) implements TypeExpression {

    /** pgvector distance operator classes, keyed by metric name. */
    private static final Map<String, String> VECTOR_OPS = Map.of(
            "cosine", "vector_cosine_ops",
            "l2", "vector_l2_ops",
            "ip", "vector_ip_ops");

    public Vector {
        if (dim <= 0) {
            throw new IllegalArgumentException(
                    "VECTOR dim must be a positive integer, got " + dim);
        }
        if (metric == null || !VECTOR_OPS.containsKey(metric)) {
            throw new IllegalArgumentException(
                    "VECTOR metric must be one of [cosine, l2, ip], got "
                            + (metric == null ? "null" : "'" + metric + "'"));
        }
    }

    /** pgvector operator class for the HNSW index. */
    public String hnswOps() {
        return VECTOR_OPS.get(metric);
    }

    @Override
    public String toString() {
        return "vector<" + dim + "," + metric + ">";
    }
}
