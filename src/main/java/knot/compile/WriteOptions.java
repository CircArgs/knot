package knot.compile;

/**
 * Keyword arguments for {@link Write} emitters.
 *
 * <p>Mirrors the Python {@code schema="knot_data", bindings_suffix="_bindings"}
 * keyword-only parameters on every {@code emit_*_sql} function.
 *
 * @param schema         postgres schema for the data plane (default {@code "knot_data"})
 * @param bindingsSuffix suffix appended to the lower-case class name to form the
 *                       bindings table name (default {@code "_bindings"})
 */
public record WriteOptions(String schema, String bindingsSuffix) {

    public WriteOptions {
        if (schema == null || schema.isBlank()) {
            throw new IllegalArgumentException("WriteOptions.schema must be non-blank");
        }
        if (bindingsSuffix == null) {
            throw new IllegalArgumentException("WriteOptions.bindingsSuffix must be non-null");
        }
    }

    /** Python default values: {@code schema="knot_data"}, {@code bindings_suffix="_bindings"}. */
    public static WriteOptions defaults() {
        return new WriteOptions("knot_data", "_bindings");
    }
}
