package knot.compile;

import java.util.List;

/**
 * JOIN alias derivation for FK chain reads.
 *
 * <p>A stable, deterministic alias for each step of an FK chain so that two FK slots pointing at
 * the same target class produce distinct JOIN aliases — e.g. {@code movie_director} vs
 * {@code movie_writer} — while the same chain referenced multiple times in one query still
 * produces a single JOIN.
 *
 * <p>Both {@link QueryCompiler} (JOIN emission) and {@link ExprCompiler} ({@code FkChainRef}
 * rendering) call {@link #chainAlias} with the same arguments, so alias assignment is pure and
 * consistent without passing a map around.
 *
 * <p>Ports {@code knot/compile/_aliases.py}.
 */
public final class Aliases {

    private Aliases() {}

    /**
     * Return the SQL alias for the JOIN introduced by {@code chain}.
     *
     * <p>The alias is derived purely from the FK path — source class name followed by each FK slot
     * name in the chain, joined with underscores. The target class name is intentionally excluded
     * so that two different FK slots pointing at the same target get distinct aliases.
     *
     * <p>Examples:
     *
     * <pre>
     *   chainAlias("Movie", List.of(new FkChainRef.Hop("director", "Person")))
     *   // → "movie_director"
     *
     *   chainAlias("Movie", List.of(new FkChainRef.Hop("writer", "Person")))
     *   // → "movie_writer"
     *
     *   chainAlias("Movie", List.of(
     *       new FkChainRef.Hop("director", "Person"),
     *       new FkChainRef.Hop("employer", "Company")))
     *   // → "movie_director_employer"
     * </pre>
     *
     * @param sourceClass the class that owns the first FK slot
     * @param chain       prefix of hops up to and including the hop being aliased
     */
    public static String chainAlias(String sourceClass, List<knot.ast.expr.FkChainRef.Hop> chain) {
        var sb = new StringBuilder(sourceClass.toLowerCase());
        for (var hop : chain) {
            sb.append('_').append(hop.fkSlotName());
        }
        return sb.toString();
    }
}
