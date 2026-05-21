package knot.spec;

import java.util.List;

/**
 * Per-slot mapping in a {@link SourceBinding}.
 *
 * <p>Declares how one class slot's value is computed from raw source fields:
 *
 * <ul>
 *   <li>{@code sourceSlot} — list of raw source field names this mapping
 *       references. The Python source's bare-string convenience
 *       (a single source field) is preserved via the
 *       {@link #of(String, String)} factory.</li>
 *   <li>{@code sql} — optional postgres expression evaluated server-side over
 *       the {@code sourceSlot} fields. {@code null} means passthrough of
 *       {@code sourceSlot.get(0)}.</li>
 * </ul>
 *
 * <p>Weight is declared separately on the owning {@link SourceBinding};
 * it's not part of the ingest-mapping shape.
 */
public record SlotMapping(String classSlot, List<String> sourceSlot, String sql) {

    public SlotMapping {
        Names.checkName("SlotMapping.classSlot", classSlot);
        if (sourceSlot == null || sourceSlot.isEmpty()) {
            throw new IllegalArgumentException(
                    "SlotMapping for '" + classSlot + "' requires at least one sourceSlot");
        }
        for (var s : sourceSlot) {
            if (s == null || s.isEmpty()) {
                throw new IllegalArgumentException(
                        "SlotMapping sourceSlot entries must all be non-empty strings");
            }
        }
        sourceSlot = List.copyOf(sourceSlot);
    }

    /** Bare passthrough mapping — one source field, no SQL transform. */
    public static SlotMapping of(String classSlot, String sourceSlot) {
        return new SlotMapping(classSlot, List.of(sourceSlot), null);
    }

    /** Multi-field mapping with an explicit SQL transform. */
    public static SlotMapping of(String classSlot, List<String> sourceSlot, String sql) {
        return new SlotMapping(classSlot, sourceSlot, sql);
    }

    /**
     * Postgres expression to evaluate — explicit {@code sql} if set,
     * otherwise bare {@code sourceSlot.get(0)} passthrough.
     */
    public String effectiveSql() {
        return sql != null ? sql : sourceSlot.get(0);
    }
}
