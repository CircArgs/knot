package knot.spec;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import knot.ast.types.Primitive;
import org.junit.jupiter.api.Test;

/**
 * Pure construction + accessor tests for {@link SourceBinding}. No I/O, no SQL emission.
 * Mirrors the binding sections of the Python {@code test_spec.py}.
 */
class SourceBindingTest {

    // -------------------------------------------------------------------------
    // Fixture helpers
    // -------------------------------------------------------------------------

    private Spec movieSpec() {
        var spec = new Spec("canonical_id");
        var movie = spec.addClass("Movie");
        movie.slot("year", Primitive.INTEGER);
        movie.slot("title", Primitive.TEXT, true, false, null);
        spec.addSource("imdb").bind(movie);
        return spec;
    }

    // -------------------------------------------------------------------------
    // Duplicate binding rejection
    // -------------------------------------------------------------------------

    @Test
    void duplicateBindingPairRejected() {
        var spec = movieSpec();
        var movie = (OntologyClass) spec.classes().get("Movie");
        var imdb = spec.sources().get("imdb");
        assertThatThrownBy(() -> imdb.bind(movie))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("already has a binding");
    }

    // -------------------------------------------------------------------------
    // Accessors
    // -------------------------------------------------------------------------

    @Test
    void bindingTableNameMatchesClass() {
        var spec = movieSpec();
        var movie = (OntologyClass) spec.classes().get("Movie");
        var imdb = spec.sources().get("imdb");
        var binding = movie.bindingFor(imdb);
        assertThat(binding).isNotNull();
        assertThat(binding.bindingsTableName()).isEqualTo(movie.bindingsTableName());
    }

    @Test
    void ontologyClassAccessor() {
        var spec = movieSpec();
        var movie = (OntologyClass) spec.classes().get("Movie");
        var imdb = spec.sources().get("imdb");
        var binding = movie.bindingFor(imdb);
        assertThat(binding.ontologyClass()).isSameAs(movie);
    }

    @Test
    void sourceAccessor() {
        var spec = movieSpec();
        var movie = (OntologyClass) spec.classes().get("Movie");
        var imdb = spec.sources().get("imdb");
        var binding = movie.bindingFor(imdb);
        assertThat(binding.source()).isSameAs(imdb);
    }

    @Test
    void identifierSlotDelegatesToClass() {
        var spec = movieSpec();
        var movie = (OntologyClass) spec.classes().get("Movie");
        var imdb = spec.sources().get("imdb");
        var binding = movie.bindingFor(imdb);
        assertThat(binding.identifierSlot().name()).isEqualTo("canonical_id");
    }

    // -------------------------------------------------------------------------
    // Slot mapping
    // -------------------------------------------------------------------------

    @Test
    void slotPassthroughMapping() {
        var spec = movieSpec();
        var movie = (OntologyClass) spec.classes().get("Movie");
        var imdb = spec.sources().get("imdb");
        var binding = movie.bindingFor(imdb);
        binding.slot("year");
        var mapping = binding.slotMappings().get("year");
        assertThat(mapping).isNotNull();
        assertThat(mapping.classSlot()).isEqualTo("year");
        assertThat(mapping.sourceSlot()).containsExactly("year");
        assertThat(mapping.sql()).isNull();
    }

    @Test
    void slotExplicitSourceAndSqlMapping() {
        var spec = movieSpec();
        var movie = (OntologyClass) spec.classes().get("Movie");
        var imdb = spec.sources().get("imdb");
        var binding = movie.bindingFor(imdb);
        binding.slot("year", "release_year", "CAST(release_year AS INT)");
        var mapping = binding.slotMappings().get("year");
        assertThat(mapping.sourceSlot()).containsExactly("release_year");
        assertThat(mapping.sql()).isEqualTo("CAST(release_year AS INT)");
    }

    @Test
    void slotMappingRejectsUnknownClassSlot() {
        var spec = movieSpec();
        var movie = (OntologyClass) spec.classes().get("Movie");
        var imdb = spec.sources().get("imdb");
        var binding = movie.bindingFor(imdb);
        assertThatThrownBy(() -> binding.slot("nonexistent_slot"))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("nonexistent_slot");
    }

    @Test
    void effectiveMappingReturnsExplicitWhenPresent() {
        var spec = movieSpec();
        var movie = (OntologyClass) spec.classes().get("Movie");
        var binding = movie.bindingFor(spec.sources().get("imdb"));
        binding.slot("year", "release_year", null);
        var m = binding.effectiveMapping("year");
        assertThat(m.sourceSlot()).containsExactly("release_year");
    }

    @Test
    void effectiveMappingReturnsImplicitPassthroughWhenNotDeclared() {
        var spec = movieSpec();
        var movie = (OntologyClass) spec.classes().get("Movie");
        var binding = movie.bindingFor(spec.sources().get("imdb"));
        // "title" has no explicit mapping
        var m = binding.effectiveMapping("title");
        assertThat(m.classSlot()).isEqualTo("title");
        assertThat(m.sourceSlot()).containsExactly("title");
        assertThat(m.sql()).isNull();
    }

    // -------------------------------------------------------------------------
    // bindingFor / bindings property
    // -------------------------------------------------------------------------

    @Test
    void bindingForUnboundSourceReturnsNull() {
        var spec = movieSpec();
        var movie = (OntologyClass) spec.classes().get("Movie");
        var tmdb = spec.addSource("tmdb");
        assertThat(movie.bindingFor(tmdb)).isNull();
    }

    @Test
    void sourceBindingsProperty() {
        var spec = movieSpec();
        var imdb = spec.sources().get("imdb");
        var bindings = imdb.bindings();
        assertThat(bindings).hasSize(1);
        assertThat(bindings.get(0).source()).isSameAs(imdb);
    }

    // -------------------------------------------------------------------------
    // requireSpec guard
    // -------------------------------------------------------------------------

    @Test
    void requireSpecThrowsWhenNotAttached() {
        // Build a SourceBinding via the package-private constructor with a detached source.
        var source = new Source("imdb", null);  // no spec attached
        var cls = new OntologyClass("Movie");
        var binding = new SourceBinding(source, cls);
        assertThatThrownBy(binding::_requireSpec)
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("not attached to a Spec");
    }
}
