package knot.compile;

import static knot.ast.expr.Expressions.*;
import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.util.List;

import org.junit.jupiter.api.Test;

import knot.ast.expr.AggExpr;
import knot.ast.expr.Aggregate;
import knot.ast.expr.Between;
import knot.ast.expr.BoolOp;
import knot.ast.expr.Compare;
import knot.ast.expr.CountRel;
import knot.ast.expr.Exists;
import knot.ast.expr.Expressions;
import knot.ast.expr.FkChainRef;
import knot.ast.expr.FkRef;
import knot.ast.expr.InList;
import knot.ast.expr.IsNull;
import knot.ast.expr.Literal;
import knot.ast.expr.Not;
import knot.ast.expr.Raw;
import knot.ast.expr.Ref;
import knot.ast.expr.TargetExists;
import knot.ast.expr.This;
import knot.ast.expr.TupleCompare;
import knot.ast.expr.TupleIn;
import knot.ast.expr.VectorDistance;
import knot.ast.expr.VectorRef;
import knot.ast.select.Layer;

/**
 * Unit tests for {@link ExprCompiler#compileSql} — ported from
 * {@code tests/unit/test_compile_*.py} and {@code test_select.py}.
 * SQL shape only (substring / equality matches); no SQL parser dep.
 */
class ExprCompilerTest {

    private static final String SCHEMA = "knot_data";
    private static final Layer RESOLVED = Layer.RESOLVED;

    // ------------------------------------------------------------------
    // Basic ref nodes
    // ------------------------------------------------------------------

    @Test
    void refRendersSchemaClassLayerSlot() {
        var ref = new Ref("Movie", "title");
        assertThat(ExprCompiler.compileSql(ref, SCHEMA, RESOLVED))
                .isEqualTo("knot_data.movie_resolved.title");
    }

    @Test
    void refAllLayers() {
        var ref = new Ref("Movie", "year");
        assertThat(ExprCompiler.compileSql(ref, SCHEMA, Layer.BINDINGS))
                .isEqualTo("knot_data.movie_bindings.year");
        assertThat(ExprCompiler.compileSql(ref, SCHEMA, Layer.ALL_SOURCES))
                .isEqualTo("knot_data.movie_all_sources.year");
        assertThat(ExprCompiler.compileSql(ref, SCHEMA, Layer.CANONICAL))
                .isEqualTo("knot_data.movie.year");
    }

    @Test
    void fkRefRendersAsColumnOnSourceTable() {
        var ref = new FkRef("Movie", "director", "Person");
        assertThat(ExprCompiler.compileSql(ref, SCHEMA, RESOLVED))
                .isEqualTo("knot_data.movie_resolved.director");
    }

    @Test
    void vectorRefRendersLikeRef() {
        var ref = new VectorRef("Movie", "title_embedding", "cosine", 384);
        assertThat(ExprCompiler.compileSql(ref, SCHEMA, RESOLVED))
                .isEqualTo("knot_data.movie_resolved.title_embedding");
    }

    @Test
    void fkChainRefRendersViaAlias() {
        var chain = new FkChainRef(
                "Movie",
                List.of(new FkChainRef.Hop("director", "Person")),
                "name");
        assertThat(ExprCompiler.compileSql(chain, SCHEMA, RESOLVED))
                .isEqualTo("movie_director.name");
    }

    @Test
    void fkChainRefMultiHopRendersViaFinalAlias() {
        var chain = new FkChainRef(
                "Movie",
                List.of(
                        new FkChainRef.Hop("director", "Person"),
                        new FkChainRef.Hop("employer", "Company")),
                "name");
        assertThat(ExprCompiler.compileSql(chain, SCHEMA, RESOLVED))
                .isEqualTo("movie_director_employer.name");
    }

    // ------------------------------------------------------------------
    // Literal / Raw
    // ------------------------------------------------------------------

    @Test
    void literalNull() {
        assertThat(ExprCompiler.compileSql(new Literal(null), SCHEMA, RESOLVED))
                .isEqualTo("NULL");
    }

    @Test
    void literalString() {
        assertThat(ExprCompiler.compileSql(new Literal("hello"), SCHEMA, RESOLVED))
                .isEqualTo("'hello'");
    }

    @Test
    void literalStringEscapesQuotes() {
        assertThat(ExprCompiler.compileSql(new Literal("O'Brien"), SCHEMA, RESOLVED))
                .isEqualTo("'O''Brien'");
    }

    @Test
    void literalIntegerAndBoolean() {
        assertThat(ExprCompiler.compileSql(new Literal(42), SCHEMA, RESOLVED)).isEqualTo("42");
        assertThat(ExprCompiler.compileSql(new Literal(true), SCHEMA, RESOLVED)).isEqualTo("TRUE");
        assertThat(ExprCompiler.compileSql(new Literal(false), SCHEMA, RESOLVED)).isEqualTo("FALSE");
    }

    @Test
    void rawPassesThrough() {
        assertThat(ExprCompiler.compileSql(new Raw("1 = 1"), SCHEMA, RESOLVED))
                .isEqualTo("1 = 1");
    }

    // ------------------------------------------------------------------
    // Boolean operators
    // ------------------------------------------------------------------

    @Test
    void compareRendersInfix() {
        var node = new Compare(">=", new Ref("Movie", "year"), new Literal(1888));
        assertThat(ExprCompiler.compileSql(node, SCHEMA, RESOLVED))
                .isEqualTo("knot_data.movie_resolved.year >= 1888");
    }

    @Test
    void boolOpWrapsParens() {
        var left = new Compare(">=", new Ref("Movie", "year"), new Literal(1900));
        var right = new Compare("<=", new Ref("Movie", "year"), new Literal(2000));
        var and = new BoolOp("AND", left, right);
        var sql = ExprCompiler.compileSql(and, SCHEMA, RESOLVED);
        assertThat(sql).contains("(knot_data.movie_resolved.year >= 1900)");
        assertThat(sql).contains("(knot_data.movie_resolved.year <= 2000)");
        assertThat(sql).contains(" AND ");
    }

    @Test
    void notWrapsParens() {
        var inner = new Compare("=", new Ref("Movie", "year"), new Literal(2020));
        var sql = ExprCompiler.compileSql(new Not(inner), SCHEMA, RESOLVED);
        assertThat(sql).isEqualTo("NOT (knot_data.movie_resolved.year = 2020)");
    }

    @Test
    void isNullAndIsNotNull() {
        var ref = new Ref("Movie", "year");
        assertThat(ExprCompiler.compileSql(new IsNull(ref, false), SCHEMA, RESOLVED))
                .isEqualTo("knot_data.movie_resolved.year IS NULL");
        assertThat(ExprCompiler.compileSql(new IsNull(ref, true), SCHEMA, RESOLVED))
                .isEqualTo("knot_data.movie_resolved.year IS NOT NULL");
    }

    @Test
    void inListAndNotIn() {
        var ref = new Ref("Movie", "year");
        var inNode = new InList(ref, List.of(1990, 2000, 2010), false);
        var sql = ExprCompiler.compileSql(inNode, SCHEMA, RESOLVED);
        assertThat(sql).contains("IN (1990, 2000, 2010)");
        assertThat(sql).doesNotContain("NOT IN");

        var notIn = new InList(ref, List.of(1990, 2000), true);
        assertThat(ExprCompiler.compileSql(notIn, SCHEMA, RESOLVED)).contains("NOT IN");
    }

    @Test
    void between() {
        var ref = new Ref("Movie", "year");
        var sql = ExprCompiler.compileSql(new Between(ref, 1900, 2000), SCHEMA, RESOLVED);
        assertThat(sql).isEqualTo("knot_data.movie_resolved.year BETWEEN 1900 AND 2000");
    }

    // ------------------------------------------------------------------
    // This — outer-scope reference
    // ------------------------------------------------------------------

    @Test
    void thisOutsideAggregateThrows() {
        var t = new This("Person");
        assertThatThrownBy(() -> ExprCompiler.compileSql(t, SCHEMA, RESOLVED))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("this.Person used outside");
    }

    @Test
    void thisWithMatchingOuterClassRendersCanonicalId() {
        var t = new This("Person");
        var sql = ExprCompiler.compileSql(t, SCHEMA, RESOLVED, "Person");
        assertThat(sql).isEqualTo("knot_data.person_resolved.canonical_id");
    }

    @Test
    void thisWithWrongOuterClassThrows() {
        var t = new This("Movie");
        assertThatThrownBy(() -> ExprCompiler.compileSql(t, SCHEMA, RESOLVED, "Person"))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("doesn't match the enclosing class");
    }

    // ------------------------------------------------------------------
    // TargetExists
    // ------------------------------------------------------------------

    @Test
    void targetExistsRendersExists() {
        var node = new TargetExists("Credit", "movie", "Movie", "canonical_id", false);
        var sql = ExprCompiler.compileSql(node, "kd", RESOLVED);
        assertThat(sql).startsWith("EXISTS");
        assertThat(sql).contains("kd.movie_resolved");
        assertThat(sql).contains("kd.movie_resolved.canonical_id = kd.credit_resolved.movie");
    }

    @Test
    void targetExistsNegatedRendersNotExists() {
        var node = new TargetExists("Credit", "movie", "Movie", "canonical_id", true);
        var sql = ExprCompiler.compileSql(node, "kd", RESOLVED);
        assertThat(sql).startsWith("NOT EXISTS");
    }

    // ------------------------------------------------------------------
    // Aggregate — any / none / count / all
    // ------------------------------------------------------------------

    @Test
    void aggregateAnyRendersExists() {
        // (movie.director == this.Person).any_()
        var pred = new Compare("=",
                new FkRef("Movie", "director", "Person"),
                new This("Person"));
        var agg = new Aggregate("any", pred, null);
        var sql = ExprCompiler.compileSql(agg, SCHEMA, RESOLVED, "Person");
        assertThat(sql).startsWith("EXISTS (SELECT 1 FROM knot_data.movie_resolved WHERE");
        assertThat(sql).contains("knot_data.movie_resolved.director = knot_data.person_resolved.canonical_id");
    }

    @Test
    void aggregateNoneRendersNotExists() {
        var pred = new Compare("=",
                new FkRef("Movie", "director", "Person"),
                new This("Person"));
        var agg = new Aggregate("none", pred, null);
        var sql = ExprCompiler.compileSql(agg, SCHEMA, RESOLVED, "Person");
        assertThat(sql).startsWith("NOT EXISTS (SELECT 1 FROM knot_data.movie_resolved WHERE");
    }

    @Test
    void aggregateCountRendersSubquery() {
        var pred = new Compare("=",
                new FkRef("Movie", "director", "Person"),
                new This("Person"));
        var agg = new Aggregate("count", pred, null);
        var sql = ExprCompiler.compileSql(agg, SCHEMA, RESOLVED, "Person");
        assertThat(sql).startsWith("(SELECT COUNT(*) FROM knot_data.movie_resolved WHERE");
    }

    @Test
    void aggregateAllRendersNotExistsAndNotCondition() {
        var pred = new Compare("=",
                new FkRef("Movie", "director", "Person"),
                new This("Person"));
        var cond = new Compare(">=", new Ref("Movie", "year"), new Literal(1900));
        var agg = new Aggregate("all", pred, cond);
        var sql = ExprCompiler.compileSql(agg, SCHEMA, RESOLVED, "Person");
        assertThat(sql).startsWith("NOT EXISTS (SELECT 1 FROM knot_data.movie_resolved WHERE");
        assertThat(sql).contains("AND NOT (");
        assertThat(sql).contains("knot_data.movie_resolved.year >= 1900");
    }

    // ------------------------------------------------------------------
    // AggExpr — COUNT / SUM / AVG / MIN / MAX + FILTER
    // ------------------------------------------------------------------

    @Test
    void aggExprCountStarRenders() {
        var sql = ExprCompiler.compileSql(count(), SCHEMA, RESOLVED);
        assertThat(sql).isEqualTo("COUNT(*)");
    }

    @Test
    void aggExprCountColumnRenders() {
        var sql = ExprCompiler.compileSql(count(new Ref("Movie", "year")), SCHEMA, RESOLVED);
        assertThat(sql).isEqualTo("COUNT(knot_data.movie_resolved.year)");
    }

    @Test
    void aggExprSumAvgMinMax() {
        assertThat(ExprCompiler.compileSql(sum_(new Ref("Movie", "year")), SCHEMA, RESOLVED))
                .isEqualTo("SUM(knot_data.movie_resolved.year)");
        assertThat(ExprCompiler.compileSql(avg(new Ref("Movie", "year")), SCHEMA, RESOLVED))
                .isEqualTo("AVG(knot_data.movie_resolved.year)");
        assertThat(ExprCompiler.compileSql(min_(new Ref("Movie", "year")), SCHEMA, RESOLVED))
                .isEqualTo("MIN(knot_data.movie_resolved.year)");
        assertThat(ExprCompiler.compileSql(max_(new Ref("Movie", "year")), SCHEMA, RESOLVED))
                .isEqualTo("MAX(knot_data.movie_resolved.year)");
    }

    @Test
    void aggExprWithFilter() {
        var filterPred = new Compare(">=", new Ref("Movie", "year"), new Literal(1900));
        var ae = new AggExpr("count", null, filterPred);
        var sql = ExprCompiler.compileSql(ae, SCHEMA, RESOLVED);
        assertThat(sql).isEqualTo(
                "COUNT(*) FILTER (WHERE knot_data.movie_resolved.year >= 1900)");
    }

    // ------------------------------------------------------------------
    // VectorDistance — literal and cross-row
    // ------------------------------------------------------------------

    @Test
    void vectorDistanceLiteralRendersWithCast() {
        var dist = new VectorDistance("Movie", "title_embedding",
                List.of(0.1, 0.2, 0.3, 0.4), "cosine", 4);
        var sql = ExprCompiler.compileSql(dist, SCHEMA, RESOLVED);
        assertThat(sql).contains("::vector(4)");
        assertThat(sql).contains("<=>");
        assertThat(sql).contains("knot_data.movie_resolved.title_embedding");
    }

    @Test
    void vectorDistanceLiteralFullFragment() {
        var dist = new VectorDistance("Movie", "title_embedding",
                List.of(0.1, 0.2, 0.3, 0.4), "cosine", 4);
        var sql = ExprCompiler.compileSql(dist, SCHEMA, RESOLVED);
        assertThat(sql).isEqualTo(
                "(knot_data.movie_resolved.title_embedding <=> '[0.1, 0.2, 0.3, 0.4]'::vector(4))");
    }

    @Test
    void vectorDistanceCrossRowRendersWithoutCast() {
        var ref = new VectorRef("Movie", "title_embedding", "cosine", 4);
        var dist = new VectorDistance("Movie", "title_embedding", ref, "cosine", 4);
        var sql = ExprCompiler.compileSql(dist, SCHEMA, RESOLVED);
        assertThat(sql).doesNotContain("::vector");
        assertThat(sql).contains("<=>");
        assertThat(sql).contains(
                "knot_data.movie_resolved.title_embedding <=> knot_data.movie_resolved.title_embedding");
    }

    @Test
    void vectorDistanceL2UsesArrowOperator() {
        var dist = new VectorDistance("Movie", "emb", List.of(1.0, 2.0, 3.0), "l2", 3);
        var sql = ExprCompiler.compileSql(dist, SCHEMA, RESOLVED);
        assertThat(sql).contains("<->");
    }

    @Test
    void vectorDistanceIpUsesIpOperator() {
        var dist = new VectorDistance("Movie", "emb", List.of(1.0, 2.0), "ip", 2);
        var sql = ExprCompiler.compileSql(dist, SCHEMA, RESOLVED);
        assertThat(sql).contains("<#>");
    }

    // ------------------------------------------------------------------
    // TupleCompare / TupleIn
    // ------------------------------------------------------------------

    @Test
    void tupleCompareLtRendersSql() {
        var year = new Ref("Movie", "year");
        var title = new Ref("Movie", "title");
        var node = new TupleCompare("<",
                List.of(year, title),
                List.of(new Literal(2020), new Literal("Z")));
        var sql = ExprCompiler.compileSql(node, "kd", RESOLVED);
        assertThat(sql).isEqualTo(
                "(kd.movie_resolved.year, kd.movie_resolved.title) < (2020, 'Z')");
    }

    @Test
    void tupleInRendersSql() {
        var year = new Ref("Movie", "year");
        var node = new TupleIn(
                List.of(year),
                List.of(List.of(1994), List.of(2020)),
                false);
        var sql = ExprCompiler.compileSql(node, "kd", RESOLVED);
        assertThat(sql).contains("IN");
        assertThat(sql).doesNotContain("NOT IN");
        assertThat(sql).contains("(kd.movie_resolved.year)");
        assertThat(sql).contains("(1994)");
        assertThat(sql).contains("(2020)");
    }

    @Test
    void tupleNotInRendersNotIn() {
        var year = new Ref("Movie", "year");
        var node = new TupleIn(List.of(year), List.of(List.of(1999), List.of(2000)), true);
        var sql = ExprCompiler.compileSql(node, "kd", RESOLVED);
        assertThat(sql).contains("NOT IN");
    }

    // ------------------------------------------------------------------
    // Exists / CountRel
    // ------------------------------------------------------------------

    @Test
    void existsRendersCorrectly() {
        var node = new Exists("Movie", "director", "Person", "canonical_id", null, false);
        var sql = ExprCompiler.compileSql(node, SCHEMA, RESOLVED);
        assertThat(sql).startsWith("EXISTS");
        assertThat(sql).contains("FROM knot_data.movie_resolved WHERE");
        assertThat(sql).contains("knot_data.movie_resolved.director = knot_data.person_resolved.canonical_id");
    }

    @Test
    void countRelRendersCorrectly() {
        var node = new CountRel("Movie", "director", "Person", "canonical_id", null);
        var sql = ExprCompiler.compileSql(node, SCHEMA, RESOLVED);
        assertThat(sql).isEqualTo(
                "(SELECT COUNT(*) FROM knot_data.movie_resolved WHERE "
                        + "knot_data.movie_resolved.director = knot_data.person_resolved.canonical_id)");
    }
}
