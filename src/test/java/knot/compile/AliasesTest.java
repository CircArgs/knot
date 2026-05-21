package knot.compile;

import static org.assertj.core.api.Assertions.assertThat;

import java.util.List;

import org.junit.jupiter.api.Test;

import knot.ast.expr.FkChainRef.Hop;

/**
 * Tests for {@link Aliases#chainAlias} — ported from the alias-derivation contract in
 * {@code knot/compile/_aliases.py}.
 */
class AliasesTest {

    @Test
    void singleStepAlias() {
        // chain_alias("Movie", (("director", "Person"),)) → "movie_director"
        var chain = List.of(new Hop("director", "Person"));
        assertThat(Aliases.chainAlias("Movie", chain)).isEqualTo("movie_director");
    }

    @Test
    void multiStepAlias() {
        // chain_alias("Movie", (("director", "Person"), ("employer", "Company")))
        // → "movie_director_employer"
        var chain = List.of(
                new Hop("director", "Person"),
                new Hop("employer", "Company"));
        assertThat(Aliases.chainAlias("Movie", chain)).isEqualTo("movie_director_employer");
    }

    @Test
    void twoChainsSamePrefixSharePrefix() {
        // director and writer both point at Person — aliases are distinct because
        // the FK slot name differs, not the target class name.
        var director = List.of(new Hop("director", "Person"));
        var writer = List.of(new Hop("writer", "Person"));
        assertThat(Aliases.chainAlias("Movie", director)).isEqualTo("movie_director");
        assertThat(Aliases.chainAlias("Movie", writer)).isEqualTo("movie_writer");
    }

    @Test
    void sourceClassLowercased() {
        var chain = List.of(new Hop("director", "Person"));
        // Source class is lowercased regardless of input casing.
        assertThat(Aliases.chainAlias("MOVIE", chain)).isEqualTo("movie_director");
    }
}
