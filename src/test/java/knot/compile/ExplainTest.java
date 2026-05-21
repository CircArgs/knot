package knot.compile;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import org.junit.jupiter.api.Test;

import knot.ast.expr.Raw;
import knot.ast.types.Array;
import knot.ast.types.Primitive;
import knot.spec.ClassKind;
import knot.spec.OntologyClass;
import knot.spec.Spec;

/**
 * Tests for {@link Explain} — ported from
 * {@code tests/unit/test_compile_explain.py}. SQL-shape only (substring
 * matches); no SQL parser dep.
 */
class ExplainTest {

    // -------------------------------------------------------------------------
    // Fixtures
    // -------------------------------------------------------------------------

    private static Spec simpleSpec() {
        var spec  = new Spec("canonical_id");
        var movie = spec.addClass("Movie");
        movie.slot("title", Primitive.TEXT);
        movie.slot("year", Primitive.INTEGER);
        return spec;
    }

    private static OntologyClass simpleMovie() {
        return (OntologyClass) simpleSpec().classes().get("Movie");
    }

    /**
     * Movie + Person + Credit (concrete) + Title (abstract) + DirectedMovie (virtual).
     * Mirrors the Python movie_spec fixture.
     */
    private static Spec movieSpec() {
        var spec  = new Spec("canonical_id");

        OntologyClass title = spec.addClass("Title", ClassKind.ABSTRACT, null, null, null);
        title.slot("name", Primitive.TEXT);

        OntologyClass movie = spec.addClass("Movie", ClassKind.CONCRETE, title, null, null);
        movie.slot("year", Primitive.INTEGER);
        movie.slot("genres", new Array(Primitive.TEXT));

        OntologyClass person = spec.addClass("Person");
        person.slot("name", Primitive.TEXT);

        OntologyClass credit = spec.addClass("Credit");
        credit.slot("role", Primitive.TEXT, true, false, null);
        credit.slot("movie", movie);

        movie.addVirtual("DirectedMovie",
                new Raw("EXISTS (SELECT 1 FROM credit WHERE role = 'director')"));

        var imdb = spec.addSource("imdb");
        imdb.bind(movie);
        imdb.bind(person);
        imdb.bind(credit);

        return spec;
    }

    // -------------------------------------------------------------------------
    // 1. All-slots — UNION ALL across non-identifier slots
    // -------------------------------------------------------------------------

    @Test
    void allSlotsUnionAllAcrossNonIdentifierSlots() {
        var sql = Explain.emitExplainWinnerSql(simpleMovie(), null, null);
        // Two non-identifier slots → one UNION ALL.
        assertThat(sql.split("UNION ALL", -1)).hasSize(2);
        assertThat(sql).contains("'title' AS slot_name");
        assertThat(sql).contains("'year' AS slot_name");
    }

    @Test
    void allSlotsWithManySlots() {
        var spec  = new Spec("canonical_id");
        var movie = spec.addClass("Movie");
        movie.slot("title", Primitive.TEXT);
        movie.slot("year", Primitive.INTEGER);
        movie.slot("runtime_minutes", Primitive.INTEGER);
        var sql = Explain.emitExplainWinnerSql(movie, null, null);
        // Three non-identifier slots → two UNION ALLs.
        assertThat(sql.split("UNION ALL", -1)).hasSize(3);
        assertThat(sql).contains("'runtime_minutes' AS slot_name");
    }

    // -------------------------------------------------------------------------
    // 2. Identifier slot is skipped
    // -------------------------------------------------------------------------

    @Test
    void identifierSlotNotInOutput() {
        var sql = Explain.emitExplainWinnerSql(simpleMovie(), null, null);
        assertThat(sql).doesNotContain("'canonical_id' AS slot_name");
        // canonical_id still appears as the partition key.
        assertThat(sql).contains("canonical_id");
    }

    // -------------------------------------------------------------------------
    // 3. Single-slot mode — no UNION ALL
    // -------------------------------------------------------------------------

    @Test
    void singleSlotNoUnionAll() {
        var sql = Explain.emitExplainWinnerSql(simpleMovie(), "year", null);
        assertThat(sql).doesNotContain("UNION ALL");
        assertThat(sql).contains("'year' AS slot_name");
        assertThat(sql).doesNotContain("'title' AS slot_name");
    }

    @Test
    void singleSlotTitle() {
        var sql = Explain.emitExplainWinnerSql(simpleMovie(), "title", null);
        assertThat(sql).contains("'title' AS slot_name");
        assertThat(sql).doesNotContain("'year' AS slot_name");
    }

    // -------------------------------------------------------------------------
    // 4. Slot typo raises
    // -------------------------------------------------------------------------

    @Test
    void slotTypoRaisesIllegalArgument() {
        assertThatThrownBy(() -> Explain.emitExplainWinnerSql(simpleMovie(), "nonexistent_slot", null))
                .isInstanceOf(IllegalArgumentException.class);
    }

    // -------------------------------------------------------------------------
    // 5. Abstract class raises
    // -------------------------------------------------------------------------

    @Test
    void abstractClassRaisesForExplain() {
        var spec  = movieSpec();
        var title = (OntologyClass) spec.classes().get("Title");
        assertThatThrownBy(() -> Explain.emitExplainWinnerSql(title, null, null))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("concrete classes");
    }

    // -------------------------------------------------------------------------
    // 6. Schema kwarg threads through
    // -------------------------------------------------------------------------

    @Test
    void schemaKwargThreadsThrough() {
        var sql = Explain.emitExplainWinnerSql(simpleMovie(), null, "my_schema");
        assertThat(sql).contains("my_schema.movie_bindings");
        assertThat(sql).contains("my_schema.source_weight");
        assertThat(sql).doesNotContain("knot_data");
    }

    // -------------------------------------------------------------------------
    // 7. Weight table kwarg threads through
    // -------------------------------------------------------------------------

    @Test
    void weightTableKwargThreadsThrough() {
        var sql = Explain.emitExplainWinnerSql(simpleMovie(), null, null, null, "custom_weights");
        assertThat(sql).contains("custom_weights");
        assertThat(sql).doesNotContain("source_weight");
    }

    // -------------------------------------------------------------------------
    // 8. Output columns present
    // -------------------------------------------------------------------------

    @Test
    void outputColumnsPresent() {
        var sql = Explain.emitExplainWinnerSql(simpleMovie(), null, null);
        assertThat(sql).contains("canonical_id");
        assertThat(sql).contains("slot_name");
        assertThat(sql).contains("source_name");
        assertThat(sql).contains("slot_value");
        assertThat(sql).contains("weight");
        assertThat(sql).contains("is_winner");
        assertThat(sql).contains("margin");
    }

    @Test
    void isWinnerUsesRankEq1() {
        var sql = Explain.emitExplainWinnerSql(simpleMovie(), null, null);
        assertThat(sql).contains("(rk = 1) AS is_winner");
    }

    @Test
    void marginUsesMaxMinusSecond() {
        var sql = Explain.emitExplainWinnerSql(simpleMovie(), null, null);
        assertThat(sql).contains("CASE");
        assertThat(sql).contains("max_w - COALESCE(second_w, max_w)");
    }

    // -------------------------------------------------------------------------
    // 9. ORDER BY clause present
    // -------------------------------------------------------------------------

    @Test
    void orderByClausePresent() {
        var sql = Explain.emitExplainWinnerSql(simpleMovie(), null, null);
        assertThat(sql).contains("ORDER BY canonical_id, slot_name, weight DESC NULLS LAST");
    }

    // -------------------------------------------------------------------------
    // 10. Weight JOIN uses correct triple
    // -------------------------------------------------------------------------

    @Test
    void weightJoinUsesClassName() {
        var sql = Explain.emitExplainWinnerSql(simpleMovie(), null, null);
        assertThat(sql).contains("w.class_name = 'Movie'");
        assertThat(sql).contains("w.source_name = ps.source_name");
        assertThat(sql).contains("w.slot_name = ps.slot_name");
    }

    // -------------------------------------------------------------------------
    // 11. Inherited slots included
    // -------------------------------------------------------------------------

    @Test
    void inheritedSlotsIncluded() {
        // Movie inherits 'name' from Title (abstract). It must appear in the output.
        var spec  = movieSpec();
        var movie = (OntologyClass) spec.classes().get("Movie");
        var sql   = Explain.emitExplainWinnerSql(movie, null, null);
        assertThat(sql).contains("'name' AS slot_name");
    }

    // -------------------------------------------------------------------------
    // 12. Virtual class cannot be passed — only OntologyClass accepted
    //     (VirtualClass is not OntologyClass; this tests the abstract guard)
    // -------------------------------------------------------------------------

    @Test
    void abstractClassRaises() {
        var spec  = new Spec("canonical_id");
        var title = spec.addClass("Title", ClassKind.ABSTRACT, null, null, null);
        title.slot("name", Primitive.TEXT);
        assertThatThrownBy(() -> Explain.emitExplainWinnerSql(title, null, null))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("concrete classes");
    }

    // -------------------------------------------------------------------------
    // 13. bindings_suffix kwarg threads through
    // -------------------------------------------------------------------------

    @Test
    void bindingsSuffixKwargThreadsThrough() {
        var sql = Explain.emitExplainWinnerSql(simpleMovie(), null, null, "__b", null);
        assertThat(sql).contains("movie__b");
        assertThat(sql).doesNotContain("movie_bindings");
    }
}
