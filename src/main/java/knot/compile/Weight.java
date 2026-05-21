package knot.compile;

import knot.spec.SourceBinding;

/**
 * Weight SQL emitter — upsert / read / delete rows in {@code source_weight}.
 *
 * <p>Weights are runtime-only (no spec-level seed). These methods emit SQL templates the host
 * uses to read and write per-(source, class, slot) weight values.
 *
 * <p>Mirrors {@code knot/compile/weight.py}.
 */
public final class Weight {

    private Weight() {}

    // -------------------------------------------------------------------------
    // Read
    // -------------------------------------------------------------------------

    /**
     * Return a SELECT that reads this binding's currently-stored weights — rows of
     * {@code (slot_name, weight)}.
     *
     * @param schema postgres schema (e.g. {@code "knot_data"})
     */
    public static String emitReadWeightsSql(SourceBinding b, String schema) {
        String table  = schema + ".source_weight";
        String srcLit = escapeLiteral(b.source().name());
        String clsLit = escapeLiteral(b.ontologyClass().name());
        return "SELECT slot_name, weight"
                + " FROM " + table
                + " WHERE source_name = '" + srcLit + "'"
                + " AND class_name = '" + clsLit + "';";
    }

    /**
     * Return a SELECT scoped to the owning source — rows of
     * {@code (class_name, slot_name, weight)} for every class this source has weight rows for.
     *
     * @param schema postgres schema (e.g. {@code "knot_data"})
     */
    public static String emitSourceReadWeightsSql(SourceBinding b, String schema) {
        String table  = schema + ".source_weight";
        String srcLit = escapeLiteral(b.source().name());
        return "SELECT class_name, slot_name, weight"
                + " FROM " + table
                + " WHERE source_name = '" + srcLit + "';";
    }

    // -------------------------------------------------------------------------
    // Upsert — single + bulk
    // -------------------------------------------------------------------------

    /**
     * Return an {@code INSERT … ON CONFLICT UPDATE} template for one (source, class, slot) weight
     * row. Binds two named placeholders: {@code %(slot_name)s} and {@code %(weight)s}.
     *
     * @param schema postgres schema (e.g. {@code "knot_data"})
     */
    public static String emitUpsertWeightSql(SourceBinding b, String schema) {
        String table  = schema + ".source_weight";
        String srcLit = escapeLiteral(b.source().name());
        String clsLit = escapeLiteral(b.ontologyClass().name());
        return "INSERT INTO " + table
                + " (source_name, class_name, slot_name, weight)"
                + " VALUES ('" + srcLit + "', '" + clsLit + "', %(slot_name)s, %(weight)s)"
                + " ON CONFLICT (source_name, class_name, slot_name)"
                + " DO UPDATE SET weight = EXCLUDED.weight;";
    }

    /**
     * Return an {@code INSERT … ON CONFLICT UPDATE} template that bulk-sets N weights in one
     * statement via {@code jsonb_each(%(weights)s::jsonb)}.
     *
     * @param schema postgres schema (e.g. {@code "knot_data"})
     */
    public static String emitUpsertWeightsSql(SourceBinding b, String schema) {
        String table  = schema + ".source_weight";
        String srcLit = escapeLiteral(b.source().name());
        String clsLit = escapeLiteral(b.ontologyClass().name());
        return "INSERT INTO " + table
                + " (source_name, class_name, slot_name, weight)"
                + " SELECT '" + srcLit + "', '" + clsLit + "', kv.key, (kv.value)::double precision"
                + " FROM jsonb_each(%(weights)s::jsonb) AS kv(key, value)"
                + " ON CONFLICT (source_name, class_name, slot_name)"
                + " DO UPDATE SET weight = EXCLUDED.weight;";
    }

    // -------------------------------------------------------------------------
    // Delete
    // -------------------------------------------------------------------------

    /**
     * Return a {@code DELETE FROM source_weight} statement for one specific
     * (source, class, slot) triple.
     *
     * @param slotName target slot — {@link IllegalArgumentException} if the slot is unknown
     * @param schema   postgres schema (e.g. {@code "knot_data"})
     * @throws IllegalArgumentException when {@code slotName} is not a slot of the bound class
     */
    public static String emitDeleteWeightSql(SourceBinding b, String slotName, String schema) {
        // Validate — raises KeyError equivalent (IAE) on typo.
        b.ontologyClass().getSlot(slotName);

        String table  = schema + ".source_weight";
        String srcLit = escapeLiteral(b.source().name());
        String clsLit = escapeLiteral(b.ontologyClass().name());
        String sltLit = escapeLiteral(slotName);
        return "DELETE FROM " + table
                + " WHERE source_name = '" + srcLit + "'"
                + " AND class_name = '" + clsLit + "'"
                + " AND slot_name = '" + sltLit + "';";
    }

    // -------------------------------------------------------------------------
    // Private helpers
    // -------------------------------------------------------------------------

    private static String escapeLiteral(String s) {
        return s.replace("'", "''");
    }
}
