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
 * Tests for {@link Resolver} — ported from
 * {@code tests/unit/test_compile_resolver.py}. SQL-shape only (substring
 * matches); no SQL parser dep.
 */
class ResolverTest {

    // -------------------------------------------------------------------------
    // Fixture
    // -------------------------------------------------------------------------

    /**
     * Mirrors the Python {@code movie_spec} fixture:
     * Title (abstract, contributes year + name), Movie (concrete, is_a Title),
     * Person (concrete), Credit (concrete), DirectedMovie (virtual).
     */
    private static Spec movieSpec() {
        var spec  = new Spec("canonical_id");

        OntologyClass title = spec.addClass("Title", ClassKind.ABSTRACT, null, null, null);
        title.slot("name", Primitive.TEXT);
        title.slot("year", Primitive.INTEGER);

        OntologyClass movie = spec.addClass("Movie", ClassKind.CONCRETE, title, null, null);
        movie.slot("genres", new Array(Primitive.TEXT));

        OntologyClass person = spec.addClass("Person");
        person.slot("name", Primitive.TEXT);

        OntologyClass credit = spec.addClass("Credit");
        credit.slot("role", Primitive.TEXT, true, false, null);
        credit.slot("movie", movie);
        credit.slot("person", person);

        movie.addVirtual("DirectedMovie",
                new Raw("EXISTS (SELECT 1 FROM credit WHERE role = 'director')"));

        var imdb = spec.addSource("imdb");
        var tmdb = spec.addSource("tmdb");
        imdb.bind(movie);
        tmdb.bind(movie);
        imdb.bind(person);
        imdb.bind(credit);

        return spec;
    }

    // -------------------------------------------------------------------------
    // Resolved view — structure
    // -------------------------------------------------------------------------

    @Test
    void resolvedViewsEmittedPerConcreteClass() {
        var opts  = Resolver.ResolverOptions.defaults();
        var views = Resolver.emitResolvedViews(movieSpec(), opts);
        // Title is abstract → no view; DirectedMovie is virtual → not handled here.
        // Movie, Person, Credit are concrete.
        assertThat(views).hasSize(3);
        for (var v : views) {
            assertThat(v).contains("CREATE VIEW knot_data.");
            assertThat(v).contains("_resolved AS");
        }
    }

    @Test
    void resolvedViewTargetsBindingsTable() {
        var spec  = movieSpec();
        var movie = (OntologyClass) spec.classes().get("Movie");
        var opts  = Resolver.ResolverOptions.defaults();
        var v     = Resolver.emitResolvedView(spec, movie, opts);
        assertThat(v).contains("FROM knot_data.movie_bindings");
        assertThat(v).doesNotContain("valid_to");
    }

    @Test
    void resolvedViewLeftJoinsWeightTable() {
        var spec  = movieSpec();
        var movie = (OntologyClass) spec.classes().get("Movie");
        var opts  = Resolver.ResolverOptions.defaults();
        var v     = Resolver.emitResolvedView(spec, movie, opts);

        assertThat(v).contains("LEFT JOIN knot_data.source_weight w");
        assertThat(v).contains("w.source_name = b.source_name");
        assertThat(v).contains("w.class_name = 'Movie'");
        assertThat(v).contains("w.slot_name = 'year'");
        assertThat(v).contains("COALESCE(w.weight, 0) DESC");
        // Weight literals must not be baked into the view.
        assertThat(v).doesNotContain("0.85");
    }

    @Test
    void resolvedViewNoInlineCaseWhen() {
        var spec  = new Spec("canonical_id");
        var movie = spec.addClass("Movie");
        movie.slot("year", Primitive.INTEGER);
        spec.addSource("imdb").bind(movie);
        spec.addSource("tmdb").bind(movie);

        var opts = Resolver.ResolverOptions.defaults();
        var v    = Resolver.emitResolvedView(spec, movie, opts);

        assertThat(v).doesNotContain("WHEN b.source_name");
        assertThat(v).contains("LEFT JOIN");
        assertThat(v).contains("COALESCE(w.weight, 0)");
    }

    @Test
    void resolvedViewOneRowPerCanonicalId() {
        var spec  = movieSpec();
        var movie = (OntologyClass) spec.classes().get("Movie");
        var opts  = Resolver.ResolverOptions.defaults();
        var v     = Resolver.emitResolvedView(spec, movie, opts);

        assertThat(v).contains("SELECT DISTINCT canonical_id");
    }

    @Test
    void resolvedViewInheritedSlotsPresent() {
        // Movie inherits 'name' and 'year' from Title; the resolved view should project them.
        var spec  = movieSpec();
        var movie = (OntologyClass) spec.classes().get("Movie");
        var opts  = Resolver.ResolverOptions.defaults();
        var v     = Resolver.emitResolvedView(spec, movie, opts);

        assertThat(v).contains("AS name");
        assertThat(v).contains("AS year");
        assertThat(v).contains("AS genres");
    }

    @Test
    void resolvedViewSkipsIdentifierInSelectList() {
        var spec  = movieSpec();
        var movie = (OntologyClass) spec.classes().get("Movie");
        var opts  = Resolver.ResolverOptions.defaults();
        var v     = Resolver.emitResolvedView(spec, movie, opts);

        // canonical_id appears as outer projection but NOT via 'AS canonical_id' rename.
        assertThat(v).doesNotContain("AS canonical_id");
        assertThat(v).contains("cb.canonical_id");
    }

    @Test
    void resolvedViewIfNotExistsSwapsCreate() {
        var spec  = movieSpec();
        var movie = (OntologyClass) spec.classes().get("Movie");
        var opts  = new Resolver.ResolverOptions(
                "knot_data", "_bindings", "_resolved", "_all_sources", "source_weight", true);
        var v     = Resolver.emitResolvedView(spec, movie, opts);

        assertThat(v).startsWith("CREATE OR REPLACE VIEW");
    }

    @Test
    void resolvedViewKwargsThreading() {
        var spec  = movieSpec();
        var movie = (OntologyClass) spec.classes().get("Movie");
        var opts  = new Resolver.ResolverOptions("alt", "__s", "__r", "_all_sources", "source_weight", false);
        var v     = Resolver.emitResolvedView(spec, movie, opts);

        assertThat(v).startsWith("CREATE VIEW alt.movie__r AS");
        assertThat(v).contains("FROM alt.movie__s");
    }

    @Test
    void resolvedViewRejectsAbstractClass() {
        var spec  = new Spec("canonical_id");
        var title = spec.addClass("Title", ClassKind.ABSTRACT, null, null, null);
        var opts  = Resolver.ResolverOptions.defaults();

        assertThatThrownBy(() -> Resolver.emitResolvedView(spec, title, opts))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("concrete classes");
    }

    // -------------------------------------------------------------------------
    // All-sources view
    // -------------------------------------------------------------------------

    @Test
    void allSourcesViewsEmittedPerConcreteClass() {
        var opts  = Resolver.ResolverOptions.defaults();
        var views = Resolver.emitAllSourcesViews(movieSpec(), opts);
        assertThat(views).hasSize(3);
        for (var v : views) {
            assertThat(v).contains("CREATE VIEW knot_data.");
            assertThat(v).contains("_all_sources AS");
        }
    }

    @Test
    void allSourcesViewUsesJsonbObjectAgg() {
        var spec  = movieSpec();
        var movie = (OntologyClass) spec.classes().get("Movie");
        var opts  = Resolver.ResolverOptions.defaults();
        var v     = Resolver.emitAllSourcesView(spec, movie, opts);

        assertThat(v).contains("jsonb_object_agg");
        assertThat(v).contains("jsonb_build_object('value', b.year, 'weight',");
        assertThat(v).contains("FILTER (WHERE b.year IS NOT NULL) AS year");
    }

    @Test
    void allSourcesViewPerSlotWeightJoin() {
        var spec  = movieSpec();
        var movie = (OntologyClass) spec.classes().get("Movie");
        var opts  = Resolver.ResolverOptions.defaults();
        var v     = Resolver.emitAllSourcesView(spec, movie, opts);

        assertThat(v).contains("LEFT JOIN knot_data.source_weight w_year");
        assertThat(v).contains("w_year.source_name = b.source_name");
        assertThat(v).contains("w_year.class_name = 'Movie'");
        assertThat(v).contains("w_year.slot_name = 'year'");
        assertThat(v).contains("COALESCE(w_year.weight, 0)");
    }

    @Test
    void allSourcesViewGroupsByIdentifier() {
        var spec  = movieSpec();
        var movie = (OntologyClass) spec.classes().get("Movie");
        var opts  = Resolver.ResolverOptions.defaults();
        var v     = Resolver.emitAllSourcesView(spec, movie, opts);

        assertThat(v).contains("GROUP BY b.canonical_id");
        assertThat(v).contains("WHERE b.canonical_id IS NOT NULL");
    }

    @Test
    void allSourcesViewIfNotExistsSwapsCreate() {
        var spec  = movieSpec();
        var movie = (OntologyClass) spec.classes().get("Movie");
        var opts  = new Resolver.ResolverOptions(
                "knot_data", "_bindings", "_resolved", "_all_sources", "source_weight", true);
        var v     = Resolver.emitAllSourcesView(spec, movie, opts);

        assertThat(v).startsWith("CREATE OR REPLACE VIEW");
    }

    @Test
    void allSourcesViewKwargsThreading() {
        var spec  = movieSpec();
        var movie = (OntologyClass) spec.classes().get("Movie");
        var opts  = new Resolver.ResolverOptions("alt", "__s", "_resolved", "__p", "source_weight", false);
        var v     = Resolver.emitAllSourcesView(spec, movie, opts);

        assertThat(v).startsWith("CREATE VIEW alt.movie__p AS");
        assertThat(v).contains("FROM alt.movie__s b");
    }

    @Test
    void allSourcesViewRejectsAbstractClass() {
        var spec  = new Spec("canonical_id");
        var title = spec.addClass("Title", ClassKind.ABSTRACT, null, null, null);
        var opts  = Resolver.ResolverOptions.defaults();

        assertThatThrownBy(() -> Resolver.emitAllSourcesView(spec, title, opts))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("concrete classes");
    }
}
