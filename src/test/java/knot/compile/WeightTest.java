package knot.compile;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import org.junit.jupiter.api.Test;

import knot.ast.types.Primitive;
import knot.spec.OntologyClass;
import knot.spec.Spec;
import knot.spec.SourceBinding;

/**
 * Tests for {@link Weight} — ported from
 * {@code tests/unit/test_compile_weight.py}. SQL-shape only (substring
 * matches); no SQL parser dep.
 */
class WeightTest {

    // -------------------------------------------------------------------------
    // Fixture
    // -------------------------------------------------------------------------

    private static Spec movieSpec() {
        var spec  = new Spec("canonical_id");
        var movie = spec.addClass("Movie");
        movie.slot("year", Primitive.INTEGER);
        movie.slot("title", Primitive.TEXT);
        spec.addSource("imdb").bind(movie);
        return spec;
    }

    private static SourceBinding movieBinding(Spec spec) {
        var movie = (OntologyClass) spec.classes().get("Movie");
        var imdb  = spec.sources().values().iterator().next();
        return movie.bindingFor(imdb);
    }

    private static SourceBinding movieBinding() {
        return movieBinding(movieSpec());
    }

    // -------------------------------------------------------------------------
    // Read weights — single binding
    // -------------------------------------------------------------------------

    @Test
    void readWeightsSqlSelectsForOneBinding() {
        var sql = Weight.emitReadWeightsSql(movieBinding(), "knot_data");
        assertThat(sql).contains("SELECT slot_name, weight");
        assertThat(sql).contains("FROM knot_data.source_weight");
        assertThat(sql).contains("source_name = 'imdb'");
        assertThat(sql).contains("class_name = 'Movie'");
    }

    @Test
    void readWeightsSqlRespectsSchemaOnSpec() {
        var spec    = new Spec("canonical_id", "alt");
        var movie   = spec.addClass("Movie");
        movie.slot("year", Primitive.INTEGER);
        var binding = spec.addSource("imdb").bind(movie);
        assertThat(Weight.emitReadWeightsSql(binding, "alt")).contains("FROM alt.source_weight");
    }

    // -------------------------------------------------------------------------
    // Read weights — source scope
    // -------------------------------------------------------------------------

    @Test
    void sourceReadWeightsSqlScopesToSource() {
        var sql = Weight.emitSourceReadWeightsSql(movieBinding(), "knot_data");
        assertThat(sql).contains("SELECT class_name, slot_name, weight");
        assertThat(sql).contains("source_name = 'imdb'");
        // Source scope is by source_name only — class_name is in the SELECT, not WHERE.
        var where = sql.substring(sql.indexOf("WHERE"));
        assertThat(where).doesNotContain("class_name =");
    }

    // -------------------------------------------------------------------------
    // Upsert — single slot
    // -------------------------------------------------------------------------

    @Test
    void upsertWeightSqlSingleSlot() {
        var sql = Weight.emitUpsertWeightSql(movieBinding(), "knot_data");
        assertThat(sql).contains("INSERT INTO knot_data.source_weight");
        assertThat(sql).contains("'imdb'");
        assertThat(sql).contains("'Movie'");
        assertThat(sql).contains("%(slot_name)s");
        assertThat(sql).contains("%(weight)s");
        assertThat(sql).contains("ON CONFLICT (source_name, class_name, slot_name)");
        assertThat(sql).contains("DO UPDATE SET weight = EXCLUDED.weight");
    }

    // -------------------------------------------------------------------------
    // Upsert — bulk via jsonb
    // -------------------------------------------------------------------------

    @Test
    void upsertWeightsSqlBulkViaJsonb() {
        var sql = Weight.emitUpsertWeightsSql(movieBinding(), "knot_data");
        assertThat(sql).contains("INSERT INTO knot_data.source_weight");
        assertThat(sql).contains("jsonb_each(%(weights)s::jsonb)");
        assertThat(sql).contains("ON CONFLICT (source_name, class_name, slot_name)");
        assertThat(sql).contains("DO UPDATE SET weight = EXCLUDED.weight");
    }

    // -------------------------------------------------------------------------
    // Facade methods match free functions
    // -------------------------------------------------------------------------

    @Test
    void bindingFacadeMethodsMatchFreeFunctions() {
        var b = movieBinding();
        // The spec's default schema is "knot_data" — the facade reads from spec
        // internally; the free function receives the schema explicitly.
        assertThat(b.readWeightsSql()).isEqualTo(Weight.emitReadWeightsSql(b, "knot_data"));
        assertThat(b.upsertWeightSql()).isEqualTo(Weight.emitUpsertWeightSql(b, "knot_data"));
        assertThat(b.upsertWeightsSql()).isEqualTo(Weight.emitUpsertWeightsSql(b, "knot_data"));
    }

    // -------------------------------------------------------------------------
    // Delete weight
    // -------------------------------------------------------------------------

    @Test
    void deleteWeightSqlRendersDelete() {
        var sql = Weight.emitDeleteWeightSql(movieBinding(), "year", "knot_data");
        assertThat(sql).startsWith("DELETE FROM knot_data.source_weight");
        assertThat(sql).contains("source_name = 'imdb'");
        assertThat(sql).contains("class_name = 'Movie'");
        assertThat(sql).contains("slot_name = 'year'");
    }

    @Test
    void deleteWeightSqlBadSlotRaises() {
        assertThatThrownBy(() -> Weight.emitDeleteWeightSql(movieBinding(), "nonexistent_slot", "knot_data"))
                .isInstanceOf(IllegalArgumentException.class);
    }

    @Test
    void deleteWeightSqlFacadeMatchesFreeFunction() {
        var b = movieBinding();
        assertThat(b.deleteWeightSql("title"))
                .isEqualTo(Weight.emitDeleteWeightSql(b, "title", "knot_data"));
    }
}
