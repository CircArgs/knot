package knot.compile;

import java.util.ArrayList;
import java.util.List;

import knot.spec.ClassKind;
import knot.spec.OntologyClass;
import knot.spec.Slot;
import knot.spec.VirtualClass;

/**
 * Explain-winner SQL emitter — SELECT that surfaces the per-slot argmax winner for each
 * {@code canonical_id}.
 *
 * <p>One row per (canonical_id, slot_name, source_name). Columns:
 * <ul>
 *   <li>{@code canonical_id}  — the entity being resolved</li>
 *   <li>{@code slot_name}     — which slot is being contested</li>
 *   <li>{@code source_name}   — which source makes this claim</li>
 *   <li>{@code slot_value}    — the source's claim, cast to text</li>
 *   <li>{@code weight}        — the (source, class, slot) weight; NULL if no row in source_weight</li>
 *   <li>{@code is_winner}     — TRUE iff this source has the highest weight for this pair</li>
 *   <li>{@code margin}        — for winning rows: winner_weight − second_place_weight; NULL for losers</li>
 * </ul>
 *
 * <p>Identifier slots are excluded — there is no resolution decision for canonical_id itself.
 * Virtual classes raise {@link IllegalArgumentException}; a slot-name typo raises
 * {@link IllegalArgumentException}.
 *
 * <p>Mirrors {@code knot/compile/explain.py}.
 */
public final class Explain {

    private Explain() {}

    private static final String DEFAULT_SCHEMA          = "knot_data";
    private static final String DEFAULT_BINDINGS_SUFFIX = "_bindings";
    private static final String DEFAULT_WEIGHT_TABLE    = "source_weight";

    // -------------------------------------------------------------------------
    // Public entry points
    // -------------------------------------------------------------------------

    /**
     * Return one SELECT explaining who won (and by how much) for each slot.
     *
     * @param cls            a concrete {@link OntologyClass}; virtual and abstract raise
     *                       {@link IllegalArgumentException}
     * @param slotName       when non-null, emit for that one slot only; typo →
     *                       {@link IllegalArgumentException}
     * @param schema         postgres schema; {@code null} uses {@code "knot_data"}
     * @param bindingsSuffix suffix for the bindings table; {@code null} uses {@code "_bindings"}
     * @param weightTable    name of the weight-policy table; {@code null} uses {@code "source_weight"}
     */
    public static String emitExplainWinnerSql(
            OntologyClass cls,
            String slotName,
            String schema,
            String bindingsSuffix,
            String weightTable) {

        if (cls.kind() != ClassKind.CONCRETE) {
            String kindLabel = cls.kind().value();
            throw new IllegalArgumentException(
                    "class '" + cls.name() + "' is '" + kindLabel
                            + "'; emit_explain_winner_sql is only supported for concrete classes");
        }

        if (schema        == null) schema        = DEFAULT_SCHEMA;
        if (bindingsSuffix == null) bindingsSuffix = DEFAULT_BINDINGS_SUFFIX;
        if (weightTable   == null) weightTable   = DEFAULT_WEIGHT_TABLE;

        Slot ident = cls.identifierSlot();

        List<Slot> targetSlots;
        if (slotName != null) {
            // Validate — raises IAE on typo (mirrors Python KeyError).
            cls.getSlot(slotName);
            targetSlots = new ArrayList<>();
            for (Slot s : cls.effectiveSlots()) {
                if (s.name().equals(slotName)) {
                    targetSlots.add(s);
                }
            }
        } else {
            targetSlots = new ArrayList<>();
            for (Slot s : cls.effectiveSlots()) {
                if (!s.name().equals(ident.name())) {
                    targetSlots.add(s);
                }
            }
        }

        if (targetSlots.isEmpty()) {
            throw new IllegalArgumentException(
                    "class '" + cls.name() + "' has no non-identifier slots to explain");
        }

        String bindingsTable  = schema + "." + cls.name().toLowerCase() + bindingsSuffix;
        String weightTableFq  = schema + "." + weightTable;
        String identName      = ident.name();

        // per_source CTE — one UNION ALL branch per slot, unpivoting bindings
        // into (canonical_id, slot_name, source_name, slot_value) rows.
        var unionBranches = new ArrayList<String>();
        for (Slot s : targetSlots) {
            unionBranches.add(
                    "  SELECT " + identName + ", '" + s.name() + "' AS slot_name, source_name,\n"
                    + "         (" + s.name() + ")::text AS slot_value\n"
                    + "  FROM " + bindingsTable + "\n"
                    + "  WHERE " + identName + " IS NOT NULL AND " + s.name() + " IS NOT NULL");
        }
        String perSourceBody = String.join("\n  UNION ALL\n", unionBranches);

        return "WITH per_source AS (\n"
                + perSourceBody + "\n"
                + "),\n"
                + "weighted AS (\n"
                + "  SELECT\n"
                + "    ps." + identName + ",\n"
                + "    ps.slot_name,\n"
                + "    ps.source_name,\n"
                + "    ps.slot_value,\n"
                + "    w.weight\n"
                + "  FROM per_source ps\n"
                + "  LEFT JOIN " + weightTableFq + " w\n"
                + "    ON w.source_name = ps.source_name\n"
                + "   AND w.class_name = '" + cls.name() + "'\n"
                + "   AND w.slot_name = ps.slot_name\n"
                + "),\n"
                + "ranked AS (\n"
                + "  SELECT\n"
                + "    " + identName + ",\n"
                + "    slot_name,\n"
                + "    source_name,\n"
                + "    slot_value,\n"
                + "    weight,\n"
                + "    RANK() OVER (\n"
                + "      PARTITION BY " + identName + ", slot_name\n"
                + "      ORDER BY weight DESC NULLS LAST\n"
                + "    ) AS rk,\n"
                + "    MAX(weight) OVER (\n"
                + "      PARTITION BY " + identName + ", slot_name\n"
                + "    ) AS max_w,\n"
                + "    NTH_VALUE(weight, 2) OVER (\n"
                + "      PARTITION BY " + identName + ", slot_name\n"
                + "      ORDER BY weight DESC NULLS LAST\n"
                + "      ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING\n"
                + "    ) AS second_w\n"
                + "  FROM weighted\n"
                + ")\n"
                + "SELECT\n"
                + "  " + identName + ",\n"
                + "  slot_name,\n"
                + "  source_name,\n"
                + "  slot_value,\n"
                + "  weight,\n"
                + "  (rk = 1) AS is_winner,\n"
                + "  CASE\n"
                + "    WHEN rk = 1\n"
                + "    THEN max_w - COALESCE(second_w, max_w)\n"
                + "  END AS margin\n"
                + "FROM ranked\n"
                + "ORDER BY " + identName + ", slot_name, weight DESC NULLS LAST;";
    }

    /**
     * Overload matching the original stub signature — defaults bindings suffix and weight table.
     *
     * @param cls      the class to explain
     * @param slotName when non-null, restrict to that one slot; typo → {@link IllegalArgumentException}
     * @param schema   postgres schema; {@code null} uses {@code "knot_data"}
     */
    public static String emitExplainWinnerSql(OntologyClass cls, String slotName, String schema) {
        return emitExplainWinnerSql(cls, slotName, schema, null, null);
    }

    /** All slots, schema from the owning spec (or default). */
    public static String emitExplainWinnerSql(OntologyClass cls) {
        return emitExplainWinnerSql(cls, null, null, null, null);
    }

    /** One slot, schema from the owning spec (or default). */
    public static String emitExplainWinnerSql(OntologyClass cls, String slotName) {
        return emitExplainWinnerSql(cls, slotName, null, null, null);
    }
}
