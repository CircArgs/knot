package knot.compile;

import knot.spec.SourceBinding;

/**
 * Weight SQL emitter — upsert / read / delete rows in {@code source_weight}.
 *
 * <p>Stub: this class will be fully implemented by the weight-emitter agent. The method
 * signatures are final (they match the Python {@code knot/compile/weight.py} public surface);
 * every body throws {@link UnsupportedOperationException} until the agent fills them in.
 *
 * <p>Mirrors {@code knot/compile/weight.py}.
 */
public final class Weight {

    private Weight() {}

    /**
     * INSERT … ON CONFLICT UPDATE for one (source, class, slot) weight row.
     * Binds {@code %(slot_name)s} and {@code %(weight)s}.
     */
    public static String emitUpsertWeightSql(SourceBinding b) {
        throw new UnsupportedOperationException("Weight.emitUpsertWeightSql not yet implemented");
    }

    /**
     * Bulk-set N weights in one statement via {@code jsonb_each(%(weights)s::jsonb)}.
     */
    public static String emitUpsertWeightsSql(SourceBinding b) {
        throw new UnsupportedOperationException("Weight.emitUpsertWeightsSql not yet implemented");
    }

    /**
     * SELECT this binding's currently-stored weights — rows of {@code (slot_name, weight)}.
     */
    public static String emitReadWeightsSql(SourceBinding b) {
        throw new UnsupportedOperationException("Weight.emitReadWeightsSql not yet implemented");
    }

    /**
     * DELETE the {@code (source, class, slot)} weight row.
     *
     * @param slotName  target slot — {@link IllegalArgumentException} on unknown name
     */
    public static String emitDeleteWeightSql(SourceBinding b, String slotName) {
        throw new UnsupportedOperationException("Weight.emitDeleteWeightSql not yet implemented");
    }
}
