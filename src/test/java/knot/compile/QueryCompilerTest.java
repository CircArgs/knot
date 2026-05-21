package knot.compile;

import static org.assertj.core.api.Assertions.assertThat;

import java.util.List;

import org.junit.jupiter.api.Test;

import knot.ast.expr.FkChainRef;
import knot.ast.expr.Ref;
import knot.ast.select.Layer;
import knot.ast.select.OrderBy;
import knot.ast.select.Query;
import knot.ast.types.Primitive;
import knot.spec.OntologyClass;
import knot.spec.Spec;

/**
 * Unit tests for {@link QueryCompiler#compileQuery} — ported from
 * {@code tests/unit/test_select.py}. SQL-shape only (substring matches);
 * no SQL parser dep.
 *
 * <p>Fixture: a movie spec built with {@code Spec.builder()}, matching the pattern
 * used in {@code DdlTest}.
 */
class QueryCompilerTest {

    private static final String SCHEMA = "knot_data";

    // ------------------------------------------------------------------
    // Fixture builders
    // ------------------------------------------------------------------

    /** Simple Movie spec — title + year. */
    private static Spec movieSpec() {
        Spec spec = new Spec("canonical_id");
        OntologyClass movie = spec.addClass("Movie");
        movie.slot("title", Primitive.TEXT);
        movie.slot("year", Primitive.INTEGER);
        return spec;
    }

    /** Movie with a director FK → Person. */
    private static Spec movieDirectorSpec() {
        Spec spec = new Spec("canonical_id");
        OntologyClass person = spec.addClass("Person");
        person.slot("name", Primitive.TEXT);
        person.slot("birth_country", Primitive.TEXT);
        OntologyClass movie = spec.addClass("Movie");
        movie.slot("title", Primitive.TEXT);
        movie.slot("year", Primitive.INTEGER);
        movie.slot("director", person);
        return spec;
    }

    /** Movie with TWO FK slots pointing at the same target (Person): director + writer. */
    private static Spec twoFkSpec() {
        Spec spec = new Spec("canonical_id");
        OntologyClass person = spec.addClass("Person");
        person.slot("name", Primitive.TEXT);
        person.slot("birth_country", Primitive.TEXT);
        OntologyClass movie = spec.addClass("Movie");
        movie.slot("title", Primitive.TEXT);
        movie.slot("year", Primitive.INTEGER);
        movie.slot("director", person);
        movie.slot("writer", person);
        return spec;
    }

    /** Movie → director (Person) → employer (Company): two-hop chain. */
    private static Spec multiHopSpec() {
        Spec spec = new Spec("canonical_id");
        OntologyClass company = spec.addClass("Company");
        company.slot("name", Primitive.TEXT);
        OntologyClass person = spec.addClass("Person");
        person.slot("name", Primitive.TEXT);
        person.slot("employer", company);
        OntologyClass movie = spec.addClass("Movie");
        movie.slot("title", Primitive.TEXT);
        movie.slot("director", person);
        return spec;
    }

    // ------------------------------------------------------------------
    // Basic SELECT shapes
    // ------------------------------------------------------------------

    @Test
    void bareClassIsSelectStar() {
        Spec spec = movieSpec();
        var q = new Query("Movie");
        var sql = QueryCompiler.compileQuery(q, spec, SCHEMA);
        assertThat(sql).isEqualTo("SELECT *\nFROM knot_data.movie_resolved;");
    }

    @Test
    void simpleWhere() {
        Spec spec = movieSpec();
        var year = new Ref("Movie", "year");
        var q = new Query("Movie").where(year.ge(1900));
        var sql = QueryCompiler.compileQuery(q, spec, SCHEMA);
        assertThat(sql).contains("SELECT *");
        assertThat(sql).contains("FROM knot_data.movie_resolved");
        assertThat(sql).contains("WHERE knot_data.movie_resolved.year >= 1900");
    }

    @Test
    void chainingWhereAddsAnd() {
        Spec spec = movieSpec();
        var year = new Ref("Movie", "year");
        var q = new Query("Movie").where(year.ge(1900)).where(year.le(2000));
        var sql = QueryCompiler.compileQuery(q, spec, SCHEMA);
        assertThat(sql).contains("1900");
        assertThat(sql).contains("2000");
        assertThat(sql).contains("AND");
    }

    @Test
    void orderByLimitOffset() {
        Spec spec = movieSpec();
        var year = new Ref("Movie", "year");
        var q = new Query("Movie")
                .orderBy(year, "desc")
                .limit(10)
                .offset(5);
        var sql = QueryCompiler.compileQuery(q, spec, SCHEMA);
        assertThat(sql).contains("ORDER BY knot_data.movie_resolved.year DESC");
        assertThat(sql).contains("LIMIT 10");
        assertThat(sql).contains("OFFSET 5");
    }

    @Test
    void projection() {
        Spec spec = movieSpec();
        var q = new Query("Movie").select(new Ref("Movie", "title"), new Ref("Movie", "year"));
        var sql = QueryCompiler.compileQuery(q, spec, SCHEMA);
        assertThat(sql).contains(
                "SELECT knot_data.movie_resolved.title, knot_data.movie_resolved.year");
        assertThat(sql).contains("FROM knot_data.movie_resolved");
    }

    @Test
    void groupByClausePositionedBetweenWhereAndOrderBy() {
        Spec spec = movieSpec();
        var year = new Ref("Movie", "year");
        var q = new Query("Movie")
                .where(year.ge(1900))
                .groupBy(year)
                .orderBy(year, "desc");
        var sql = QueryCompiler.compileQuery(q, spec, SCHEMA);
        int wherePos = sql.indexOf("WHERE");
        int groupPos = sql.indexOf("GROUP BY");
        int orderPos = sql.indexOf("ORDER BY");
        assertThat(wherePos).isLessThan(groupPos);
        assertThat(groupPos).isLessThan(orderPos);
        assertThat(sql).contains("GROUP BY knot_data.movie_resolved.year");
    }

    @Test
    void lockClauseAtTail() {
        Spec spec = movieSpec();
        var q = new Query("Movie").limit(50).lock("for_update_skip_locked");
        var sql = QueryCompiler.compileQuery(q, spec, SCHEMA);
        assertThat(sql).contains("LIMIT 50");
        // Lock clause must come after LIMIT.
        assertThat(sql.indexOf("FOR UPDATE SKIP LOCKED"))
                .isGreaterThan(sql.indexOf("LIMIT 50"));
        // Strip trailing semicolon for the endsWith check.
        assertThat(sql.replace(";", "").stripTrailing()).endsWith("FOR UPDATE SKIP LOCKED");
    }

    @Test
    void lockForShare() {
        Spec spec = movieSpec();
        var q = new Query("Movie").lock("for_share");
        assertThat(QueryCompiler.compileQuery(q, spec, SCHEMA)).contains("FOR SHARE");
    }

    @Test
    void layerCanonicalTargetsCanonicalTable() {
        Spec spec = movieSpec();
        var year = new Ref("Movie", "year");
        var q = new Query("Movie", year.eq(2020), List.of(), List.of(),
                null, null, null, Layer.CANONICAL, null, null);
        var sql = QueryCompiler.compileQuery(q, spec, SCHEMA);
        assertThat(sql).contains("FROM knot_data.movie\n");
        assertThat(sql).contains("knot_data.movie.year = 2020");
    }

    // ------------------------------------------------------------------
    // FK transparent walks (JOINs)
    // ------------------------------------------------------------------

    @Test
    void fkWalkInWhereProducesJoin() {
        Spec spec = movieDirectorSpec();
        // movie.col.director.birth_country == "USA"
        var chainRef = new FkChainRef(
                "Movie",
                List.of(new FkChainRef.Hop("director", "Person")),
                "birth_country");
        var q = new Query("Movie").where(chainRef.eq("USA"));
        var sql = QueryCompiler.compileQuery(q, spec, SCHEMA);
        assertThat(sql).contains(
                "JOIN knot_data.person_resolved AS movie_director "
                        + "ON movie_director.canonical_id = knot_data.movie_resolved.director");
        assertThat(sql).contains("movie_director.birth_country = 'USA'");
    }

    @Test
    void fkWalkInProjectionProducesJoin() {
        Spec spec = movieDirectorSpec();
        var title = new Ref("Movie", "title");
        var dirName = new FkChainRef(
                "Movie",
                List.of(new FkChainRef.Hop("director", "Person")),
                "name");
        var q = new Query("Movie").select(title, dirName);
        var sql = QueryCompiler.compileQuery(q, spec, SCHEMA);
        assertThat(sql).contains(
                "SELECT knot_data.movie_resolved.title, movie_director.name");
        assertThat(sql).contains("JOIN knot_data.person_resolved AS movie_director");
    }

    @Test
    void fkWalkInOrderByProducesJoin() {
        Spec spec = movieDirectorSpec();
        var dirName = new FkChainRef(
                "Movie",
                List.of(new FkChainRef.Hop("director", "Person")),
                "name");
        var q = new Query("Movie").orderBy(dirName, "desc");
        var sql = QueryCompiler.compileQuery(q, spec, SCHEMA);
        assertThat(sql).contains("ORDER BY movie_director.name DESC");
        assertThat(sql).contains("JOIN knot_data.person_resolved AS movie_director");
    }

    @Test
    void sameFkChainReferencedMultipleTimesProducesOneJoin() {
        Spec spec = movieDirectorSpec();
        var dirName = new FkChainRef(
                "Movie",
                List.of(new FkChainRef.Hop("director", "Person")),
                "name");
        var dirCountry = new FkChainRef(
                "Movie",
                List.of(new FkChainRef.Hop("director", "Person")),
                "birth_country");
        var q = new Query("Movie")
                .where(dirCountry.eq("USA"))
                .orderBy(dirName, "asc")
                .select(new Ref("Movie", "title"), dirName);
        var sql = QueryCompiler.compileQuery(q, spec, SCHEMA);
        // Exactly one JOIN
        assertThat(sql.split("JOIN knot_data.person_resolved AS movie_director", -1).length - 1)
                .isEqualTo(1);
    }

    @Test
    void twoFksSameTargetProduceTwoJoins() {
        // Alex's collision case: director AND writer both point at Person.
        Spec spec = twoFkSpec();
        var dirName = new FkChainRef(
                "Movie",
                List.of(new FkChainRef.Hop("director", "Person")),
                "name");
        var writerName = new FkChainRef(
                "Movie",
                List.of(new FkChainRef.Hop("writer", "Person")),
                "name");
        var q = new Query("Movie").select(new Ref("Movie", "title"), dirName, writerName);
        var sql = QueryCompiler.compileQuery(q, spec, SCHEMA);

        assertThat(sql).contains("JOIN knot_data.person_resolved AS movie_director");
        assertThat(sql).contains("JOIN knot_data.person_resolved AS movie_writer");
        // Two distinct joins
        assertThat(sql.split("JOIN knot_data.person_resolved", -1).length - 1).isEqualTo(2);
        assertThat(sql).contains("movie_director.name");
        assertThat(sql).contains("movie_writer.name");
        // No bare unaliased references
        assertThat(sql).doesNotContain("knot_data.person_resolved.name");
    }

    @Test
    void multiHopChainProducesChainedJoins() {
        // Movie → director (Person) → employer (Company): two JOINs, each ON previous alias.
        Spec spec = multiHopSpec();
        var companyName = new FkChainRef(
                "Movie",
                List.of(
                        new FkChainRef.Hop("director", "Person"),
                        new FkChainRef.Hop("employer", "Company")),
                "name");
        var q = new Query("Movie").select(new Ref("Movie", "title"), companyName);
        var sql = QueryCompiler.compileQuery(q, spec, SCHEMA);

        // First hop
        assertThat(sql).contains("JOIN knot_data.person_resolved AS movie_director");
        assertThat(sql).contains(
                "ON movie_director.canonical_id = knot_data.movie_resolved.director");
        // Second hop references the first alias as its lhs
        assertThat(sql).contains("JOIN knot_data.company_resolved AS movie_director_employer");
        assertThat(sql).contains(
                "ON movie_director_employer.canonical_id = movie_director.employer");
        // Terminal column uses final alias
        assertThat(sql).contains("movie_director_employer.name");
    }

    @Test
    void tupleInRendersCorrectly() {
        Spec spec = movieSpec();
        var year = new Ref("Movie", "year");
        var title = new Ref("Movie", "title");
        var node = new knot.ast.expr.TupleIn(
                List.of(year, title),
                List.of(List.of(2020, "Tenet"), List.of(1994, "Pulp Fiction")),
                false);
        var q = new Query("Movie").where(node);
        var sql = QueryCompiler.compileQuery(q, spec, SCHEMA);
        assertThat(sql).contains("(knot_data.movie_resolved.year, knot_data.movie_resolved.title)");
        assertThat(sql).contains("IN");
        assertThat(sql).contains("(2020, 'Tenet')");
        assertThat(sql).contains("(1994, 'Pulp Fiction')");
    }

    @Test
    void tupleCompareRendersCorrectly() {
        Spec spec = movieSpec();
        var year = new Ref("Movie", "year");
        var node = new knot.ast.expr.TupleCompare(
                "<",
                List.of(year),
                List.of(new knot.ast.expr.Literal(2020)));
        var q = new Query("Movie").where(node);
        var sql = QueryCompiler.compileQuery(q, spec, SCHEMA);
        assertThat(sql).contains("(knot_data.movie_resolved.year) < (2020)");
    }
}
