package knot.ast.select;

/**
 * Which postgres relation a {@link Query} (or constraint validation, or DDL view body)
 * targets.
 *
 * <p>The enum's {@link #suffix()} is the literal table-name suffix the compiler appends after
 * {@code <schema>.<class>} — typing the layer instead of a bare string moves typos from
 * runtime "relation does not exist" errors to construction-time failures.
 *
 * <p>Mirrors the Python {@code StrEnum Layer}.
 */
public enum Layer {
    RESOLVED("_resolved"),
    ALL_SOURCES("_all_sources"),
    BINDINGS("_bindings"),
    CANONICAL("");

    private final String suffix;

    Layer(String suffix) {
        this.suffix = suffix;
    }

    /** Table-name suffix appended after {@code <schema>.<class>}. */
    public String suffix() {
        return suffix;
    }
}
