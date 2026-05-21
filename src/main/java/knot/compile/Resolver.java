package knot.compile;

import java.util.ArrayList;
import java.util.List;

import knot.spec.ClassKind;
import knot.spec.OntologyClass;
import knot.spec.Slot;
import knot.spec.Spec;

/**
 * Resolved-view and all-sources-view emitter.
 *
 * <p>For each concrete {@link OntologyClass}, emits two SQL {@code VIEW}s:
 * <ol>
 *   <li>{@code <schema>.<class>_resolved} — per-slot argmax over the bindings table × source_weight,
 *       one row per canonical_id. The resolver's runtime output.</li>
 *   <li>{@code <schema>.<class>_all_sources} — per-source provenance, one row per canonical_id with
 *       each slot as a {@code jsonb} of {@code {source_name: {value, weight}}}.</li>
 * </ol>
 *
 * <p>Weights are read from the runtime {@code source_weight} table via {@code LEFT JOIN} — opaque
 * floats; higher wins. Sources missing from that table fall to {@code COALESCE(weight, 0)} and lose
 * every tie-break.
 *
 * <p>Mirrors {@code knot/compile/resolver.py}.
 */
public final class Resolver {

    private Resolver() {}

    // -------------------------------------------------------------------------
    // Options record
    // -------------------------------------------------------------------------

    /**
     * Knobs controlling view emission.
     *
     * @param schema           postgres schema name (default {@code "knot_data"})
     * @param bindingsSuffix   suffix appended to the class name for the bindings table
     *                         (default {@code "_bindings"})
     * @param resolvedSuffix   suffix for the resolved view (default {@code "_resolved"})
     * @param allSourcesSuffix suffix for the all-sources view (default {@code "_all_sources"})
     * @param weightTableName  name of the weight-policy table (default {@code "source_weight"})
     * @param ifNotExists      when {@code true}, emit {@code CREATE OR REPLACE VIEW} instead of
     *                         {@code CREATE VIEW}
     */
    public record ResolverOptions(
            String schema,
            String bindingsSuffix,
            String resolvedSuffix,
            String allSourcesSuffix,
            String weightTableName,
            boolean ifNotExists) {

        /** Python kwarg defaults. */
        public static ResolverOptions defaults() {
            return new ResolverOptions(
                    "knot_data", "_bindings", "_resolved", "_all_sources", "source_weight", false);
        }
    }

    // -------------------------------------------------------------------------
    // Resolved view
    // -------------------------------------------------------------------------

    /**
     * Return {@code CREATE [OR REPLACE] VIEW <schema>.<class><resolvedSuffix>} for one concrete
     * class.
     *
     * <p>The view drives one correlated subquery per non-identifier slot:
     * <pre>
     *   (SELECT b.<slot>
     *    FROM <bindings_table> b
     *    LEFT JOIN <weight_table> w
     *      ON w.source_name = b.source_name
     *     AND w.class_name  = '<ClassName>'
     *     AND w.slot_name   = '<slot>'
     *    WHERE b.<ident> = cb.<ident>
     *      AND b.<slot> IS NOT NULL
     *    ORDER BY COALESCE(w.weight, 0) DESC, b.source_name
     *    LIMIT 1) AS <slot>
     * </pre>
     *
     * <p>The outer FROM enumerates {@code DISTINCT canonical_id}s that have at least one
     * ER-stamped binding.
     *
     * @throws IllegalArgumentException when {@code cls} is not concrete
     */
    public static String emitResolvedView(
            Spec spec,
            OntologyClass cls,
            String schema,
            String bindingsSuffix,
            String resolvedSuffix,
            String weightTableName,
            boolean ifNotExists) {
        if (cls.kind() != ClassKind.CONCRETE) {
            throw new IllegalArgumentException(
                    "class '" + cls.name() + "' is '" + cls.kind().value()
                            + "'; resolved views are only emitted for concrete classes");
        }

        String bindingsTable = schema + "." + cls.name().toLowerCase() + bindingsSuffix;
        String weightTable   = schema + "." + weightTableName;
        String viewName      = schema + "." + cls.name().toLowerCase() + resolvedSuffix;
        Slot   ident         = cls.identifierSlot();
        String create        = ifNotExists ? "CREATE OR REPLACE VIEW" : "CREATE VIEW";

        var selectLines = new ArrayList<String>();
        selectLines.add("    cb." + ident.name());

        for (Slot slot : cls.effectiveSlots()) {
            if (slot.name().equals(ident.name())) {
                continue;
            }
            String subq = winningValueExpr(bindingsTable, weightTable, cls.name(), ident, slot);
            selectLines.add("    " + subq + " AS " + slot.name());
        }

        return create + " " + viewName + " AS\n"
                + "SELECT\n"
                + String.join(",\n", selectLines) + "\n"
                + "FROM (\n"
                + "    SELECT DISTINCT " + ident.name() + "\n"
                + "    FROM " + bindingsTable + "\n"
                + "    WHERE " + ident.name() + " IS NOT NULL\n"
                + ") AS cb;";
    }

    /** Overload that accepts a {@link ResolverOptions} record. */
    public static String emitResolvedView(Spec spec, OntologyClass cls, ResolverOptions opts) {
        return emitResolvedView(
                spec, cls,
                opts.schema(), opts.bindingsSuffix(), opts.resolvedSuffix(),
                opts.weightTableName(), opts.ifNotExists());
    }

    /**
     * Return one {@code CREATE VIEW} per concrete class in {@code spec}.
     *
     * <p>Mirrors Python {@code emit_resolved_views}.
     */
    public static List<String> emitResolvedViews(Spec spec, ResolverOptions opts) {
        var out = new ArrayList<String>();
        for (OntologyClass cls : spec.concreteClasses()) {
            out.add(emitResolvedView(spec, cls, opts));
        }
        return out;
    }

    // -------------------------------------------------------------------------
    // All-sources view
    // -------------------------------------------------------------------------

    /**
     * Return {@code CREATE [OR REPLACE] VIEW <schema>.<class><allSourcesSuffix>} — the provenance
     * view.
     *
     * <p>Same shape as the resolved view (one row per canonical_id) but every slot column is a
     * {@code jsonb} object keyed by source name:
     * <pre>
     *   {"imdb": {"value": 1994, "weight": 0.85}, "tmdb": {"value": 1995, "weight": 0.70}}
     * </pre>
     *
     * <p>Sources contributing {@code NULL} for a slot are filtered out per slot (
     * {@code FILTER (WHERE b.<slot> IS NOT NULL)}).
     *
     * @throws IllegalArgumentException when {@code cls} is not concrete
     */
    public static String emitAllSourcesView(
            Spec spec,
            OntologyClass cls,
            String schema,
            String bindingsSuffix,
            String allSourcesSuffix,
            String weightTableName,
            boolean ifNotExists) {
        if (cls.kind() != ClassKind.CONCRETE) {
            throw new IllegalArgumentException(
                    "class '" + cls.name() + "' is '" + cls.kind().value()
                            + "'; all-sources views are only emitted for concrete classes");
        }

        String bindingsTable = schema + "." + cls.name().toLowerCase() + bindingsSuffix;
        String weightTable   = schema + "." + weightTableName;
        String viewName      = schema + "." + cls.name().toLowerCase() + allSourcesSuffix;
        Slot   ident         = cls.identifierSlot();
        String create        = ifNotExists ? "CREATE OR REPLACE VIEW" : "CREATE VIEW";

        var selectLines = new ArrayList<String>();
        selectLines.add("    b." + ident.name());

        var joinLines = new ArrayList<String>();

        for (Slot slot : cls.effectiveSlots()) {
            if (slot.name().equals(ident.name())) {
                continue;
            }
            String alias = "w_" + slot.name();
            joinLines.add(
                    "LEFT JOIN " + weightTable + " " + alias + "\n"
                    + "  ON " + alias + ".source_name = b.source_name "
                    + "AND " + alias + ".class_name = '" + cls.name() + "' "
                    + "AND " + alias + ".slot_name = '" + slot.name() + "'");
            selectLines.add(
                    "    jsonb_object_agg(\n"
                    + "      b.source_name,\n"
                    + "      jsonb_build_object('value', b." + slot.name()
                    + ", 'weight', COALESCE(" + alias + ".weight, 0))\n"
                    + "    ) FILTER (WHERE b." + slot.name() + " IS NOT NULL) AS " + slot.name());
        }

        String joins = String.join("\n", joinLines);

        return create + " " + viewName + " AS\n"
                + "SELECT\n"
                + String.join(",\n", selectLines) + "\n"
                + "FROM " + bindingsTable + " b\n"
                + joins + "\n"
                + "WHERE b." + ident.name() + " IS NOT NULL\n"
                + "GROUP BY b." + ident.name() + ";";
    }

    /** Overload that accepts a {@link ResolverOptions} record. */
    public static String emitAllSourcesView(Spec spec, OntologyClass cls, ResolverOptions opts) {
        return emitAllSourcesView(
                spec, cls,
                opts.schema(), opts.bindingsSuffix(), opts.allSourcesSuffix(),
                opts.weightTableName(), opts.ifNotExists());
    }

    /**
     * Return one {@code CREATE VIEW} per concrete class in {@code spec}.
     *
     * <p>Mirrors Python {@code emit_all_sources_views}.
     */
    public static List<String> emitAllSourcesViews(Spec spec, ResolverOptions opts) {
        var out = new ArrayList<String>();
        for (OntologyClass cls : spec.concreteClasses()) {
            out.add(emitAllSourcesView(spec, cls, opts));
        }
        return out;
    }

    // -------------------------------------------------------------------------
    // Private helpers
    // -------------------------------------------------------------------------

    /**
     * Correlated subquery that picks the winning value for {@code slot} in the resolved view.
     * The {@code LEFT JOIN} against the weight table is per-(source, class, slot) so each slot
     * gets its own weight row.
     */
    private static String winningValueExpr(
            String bindingsTable,
            String weightTable,
            String className,
            Slot ident,
            Slot slot) {
        return "(SELECT b." + slot.name() + " "
                + "FROM " + bindingsTable + " b "
                + "LEFT JOIN " + weightTable + " w "
                + "ON w.source_name = b.source_name "
                + "AND w.class_name = '" + className + "' "
                + "AND w.slot_name = '" + slot.name() + "' "
                + "WHERE b." + ident.name() + " = cb." + ident.name() + " "
                + "AND b." + slot.name() + " IS NOT NULL "
                + "ORDER BY COALESCE(w.weight, 0) DESC, b.source_name "
                + "LIMIT 1)";
    }
}
