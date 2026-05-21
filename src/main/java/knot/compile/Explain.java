package knot.compile;

import knot.spec.OntologyClass;

/**
 * Explain-winner SQL emitter — SELECT that surfaces the per-slot argmax winner for each
 * {@code canonical_id}.
 *
 * <p>Stub: this class will be fully implemented by the explain-emitter agent. The method
 * signatures are final; every body throws {@link UnsupportedOperationException} until filled in.
 *
 * <p>Mirrors {@code knot/compile/explain.py}.
 */
public final class Explain {

    private Explain() {}

    /**
     * Return a SQL SELECT explaining who won the resolver argmax for each
     * (canonical_id, slot_name) pair.
     *
     * <p>One row per (canonical_id, slot_name, source_name) with columns:
     * {@code canonical_id}, {@code slot_name}, {@code source_name}, {@code slot_value} (text),
     * {@code weight} (float or NULL), {@code is_winner} (boolean),
     * {@code margin} (winner − second for winning rows, NULL for losers).
     *
     * @param cls      the class to explain
     * @param slotName when non-null, restrict to that one slot; typo → {@link IllegalArgumentException}
     * @param schema   postgres schema
     */
    public static String emitExplainWinnerSql(OntologyClass cls, String slotName, String schema) {
        throw new UnsupportedOperationException("Explain.emitExplainWinnerSql not yet implemented");
    }
}
