package knot.compile;

import static org.assertj.core.api.Assertions.assertThat;

import java.util.List;
import java.util.Map;

import org.junit.jupiter.api.Test;

import knot.ast.select.Layer;
import knot.ast.types.Primitive;
import knot.spec.OntologyClass;
import knot.spec.Severity;
import knot.spec.Spec;

/**
 * Tests for {@link Constraints} — ported from
 * {@code tests/unit/test_compile_constraints.py}. SQL-shape only (substring
 * matches); no SQL parser dep.
 */
class ConstraintsTest {

    // -------------------------------------------------------------------------
    // Fixtures
    // -------------------------------------------------------------------------

    /** Movie spec with year_sane constraint. Mirrors the Python movie_spec fixture. */
    private static Spec movieSpec() {
        var spec  = new Spec("canonical_id");
        var movie = spec.addClass("Movie");
        movie.slot("name", Primitive.TEXT);
        movie.slot("year", Primitive.INTEGER);
        movie.addConstraint("year_sane", movie.col().get("year").ge(1888));
        return spec;
    }

    /** Person + Movie with title required and director FK. */
    private static Spec fkSpec() {
        var spec   = new Spec("canonical_id");
        var person = spec.addClass("Person");
        person.slot("name", Primitive.TEXT, true, false, null);
        var movie  = spec.addClass("Movie");
        movie.slot("title", Primitive.TEXT, true, false, null);
        movie.slot("year", Primitive.INTEGER);
        movie.slot("director", person);
        return spec;
    }

    private static Map<String, String> validationMap(Spec spec) {
        var list   = Constraints.emitValidation(spec, Constraints.ConstraintsOptions.defaults());
        var result = new java.util.LinkedHashMap<String, String>();
        for (var ns : list) {
            result.put(ns.name(), ns.sql());
        }
        return result;
    }

    // -------------------------------------------------------------------------
    // Uniform 5-column shape
    // -------------------------------------------------------------------------

    @Test
    void uniformColumnShape() {
        var rewrites = validationMap(movieSpec());
        for (var sql : rewrites.values()) {
            assertThat(sql).contains("AS rule_id");
            assertThat(sql).contains("AS class_name");
            assertThat(sql).contains("AS severity");
            assertThat(sql).contains("AS message");
            assertThat(sql).contains("AS offending_pk");
        }
    }

    @Test
    void messageNullWhenUnset() {
        var rewrites = validationMap(movieSpec());
        assertThat(rewrites.get("year_sane")).contains("NULL AS message");
    }

    @Test
    void messageLiteralWhenSet() {
        var spec  = new Spec("canonical_id");
        var movie = spec.addClass("Movie");
        movie.slot("year", Primitive.INTEGER);
        movie.addConstraint("y", movie.col().get("year").gt(0), Severity.ERROR, "must be positive");
        var rewrites = validationMap(spec);
        assertThat(rewrites.get("y")).contains("'must be positive' AS message");
    }

    @Test
    void apostropheInMessageEscaped() {
        var spec  = new Spec("canonical_id");
        var movie = spec.addClass("Movie");
        movie.slot("year", Primitive.INTEGER);
        movie.addConstraint("y", movie.col().get("year").gt(0), Severity.ERROR, "director's pick");
        var rewrites = validationMap(spec);
        assertThat(rewrites.get("y")).contains("'director''s pick'");
    }

    @Test
    void bareColumnUnchanged() {
        var rewrites = validationMap(movieSpec());
        assertThat(rewrites.get("year_sane")).contains("year >= 1888");
    }

    // -------------------------------------------------------------------------
    // scope_to_source_identifiers — delta-only validation
    // -------------------------------------------------------------------------

    @Test
    void scopeNoneProducesUnscopedSql() {
        var spec  = new Spec("canonical_id");
        var movie = spec.addClass("Movie");
        movie.slot("year", Primitive.INTEGER);
        movie.addConstraint("year_sane", movie.col().get("year").ge(1888));
        var opts  = new Constraints.ConstraintsOptions("knot_data", Layer.RESOLVED, null, true);
        var sql   = Constraints.emitValidation(spec, opts).get(0).sql();
        assertThat(sql).doesNotContain("IN (");
        assertThat(sql).doesNotContain("canonical_id IS NOT NULL");
    }

    @Test
    void scopeEmptyDictEqualsNone() {
        var spec  = new Spec("canonical_id");
        var movie = spec.addClass("Movie");
        movie.slot("year", Primitive.INTEGER);
        movie.addConstraint("year_sane", movie.col().get("year").ge(1888));

        var unscoped = Constraints.emitValidation(spec, Constraints.ConstraintsOptions.defaults()).get(0).sql();
        var opts     = new Constraints.ConstraintsOptions("knot_data", Layer.RESOLVED, Map.of(), true);
        var scoped   = Constraints.emitValidation(spec, opts).get(0).sql();
        assertThat(scoped).isEqualTo(unscoped);
    }

    @Test
    void scopeSingleSourceInlinesSubquery() {
        var spec  = new Spec("canonical_id");
        var movie = spec.addClass("Movie");
        movie.slot("year", Primitive.INTEGER);
        movie.addConstraint("year_sane", movie.col().get("year").ge(1888));

        var opts  = new Constraints.ConstraintsOptions(
                "knot_data", Layer.RESOLVED,
                Map.of("imdb", List.of("tt001", "tt002")), true);
        var sql   = Constraints.emitValidation(spec, opts).get(0).sql();

        assertThat(sql).contains("canonical_id IN");
        assertThat(sql).contains("movie_bindings");
        assertThat(sql).contains("('imdb', 'tt001')");
        assertThat(sql).contains("('imdb', 'tt002')");
        assertThat(sql).contains("canonical_id IS NOT NULL");
    }

    @Test
    void scopeMultiSourceInlinesAllPairs() {
        var spec  = new Spec("canonical_id");
        var movie = spec.addClass("Movie");
        movie.slot("year", Primitive.INTEGER);
        movie.addConstraint("year_sane", movie.col().get("year").ge(1888));

        var opts = new Constraints.ConstraintsOptions(
                "knot_data", Layer.RESOLVED,
                Map.of("imdb", List.of("tt001"), "tmdb", List.of("m999")), true);
        var sql  = Constraints.emitValidation(spec, opts).get(0).sql();

        assertThat(sql).contains("('imdb', 'tt001')");
        assertThat(sql).contains("('tmdb', 'm999')");
    }

    // -------------------------------------------------------------------------
    // include_builtins
    // -------------------------------------------------------------------------

    @Test
    void builtinFkOrphanConstraintEmitted() {
        var names = validationMap(fkSpec()).keySet();
        assertThat(names).contains("_builtin_fk_orphan_Movie_director");
    }

    @Test
    void builtinRequiredNullConstraintEmitted() {
        var names = validationMap(fkSpec()).keySet();
        assertThat(names).contains("_builtin_required_null_Movie_title");
        assertThat(names).contains("_builtin_required_null_Person_name");
    }

    @Test
    void builtinSkippedForNonRequiredNonFk() {
        var names = validationMap(fkSpec()).keySet();
        assertThat(names).doesNotContain("_builtin_required_null_Movie_year");
        assertThat(names).doesNotContain("_builtin_fk_orphan_Movie_year");
    }

    @Test
    void builtinSkippedForIdentifierSlot() {
        var names = validationMap(fkSpec()).keySet();
        assertThat(names).doesNotContain("_builtin_required_null_Movie_canonical_id");
    }

    @Test
    void includeBuiltinsFalseDropsThem() {
        var spec   = fkSpec();
        var movie  = (OntologyClass) spec.classes().get("Movie");
        movie.addConstraint("year_sane", movie.col().get("year").ge(1888));
        var opts   = new Constraints.ConstraintsOptions(
                "knot_data", Layer.RESOLVED, null, false);
        var names  = Constraints.emitValidation(spec, opts).stream()
                .map(Constraints.NamedSql::name).toList();
        assertThat(names).containsExactly("year_sane");
    }

    @Test
    void fkOrphanBodyAllowsNullFk() {
        var rewrites = validationMap(fkSpec());
        var sql      = rewrites.get("_builtin_fk_orphan_Movie_director");
        assertThat(sql).contains("IS NULL");
        assertThat(sql).contains("EXISTS");
    }

    @Test
    void builtinConstraintsHaveCorrectSeverity() {
        var rewrites = validationMap(fkSpec());
        assertThat(rewrites.get("_builtin_fk_orphan_Movie_director")).contains("'error'");
        assertThat(rewrites.get("_builtin_required_null_Movie_title")).contains("'warning'");
    }
}
