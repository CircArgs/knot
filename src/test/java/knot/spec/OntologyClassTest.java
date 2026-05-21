package knot.spec;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import knot.ast.expr.Raw;
import knot.ast.select.Layer;
import knot.ast.types.ClassRef;
import knot.ast.types.Primitive;
import org.junit.jupiter.api.Test;

/**
 * Pure construction + accessor tests for {@link OntologyClass}. No I/O, no SQL emission.
 * Mirrors the per-class and inheritance sections of the Python {@code test_spec.py}.
 */
class OntologyClassTest {

    // -------------------------------------------------------------------------
    // Slot builder
    // -------------------------------------------------------------------------

    @Test
    void duplicateSlotNameRejected() {
        var spec = new Spec("canonical_id");
        var movie = spec.addClass("Movie");
        movie.slot("year", Primitive.INTEGER);
        assertThatThrownBy(() -> movie.slot("year", Primitive.TEXT))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("already has a slot");
    }

    @Test
    void slotWithOntologyClassCreatesClassRef() {
        var spec = new Spec("canonical_id");
        var person = spec.addClass("Person");
        var movie = spec.addClass("Movie");
        var s = movie.slot("director", person);
        assertThat(s.isFk()).isTrue();
        assertThat(s.type()).isInstanceOf(ClassRef.class);
        assertThat(((ClassRef) s.type()).target()).isSameAs(person);
    }

    // -------------------------------------------------------------------------
    // Inheritance — chain(), effectiveSlots(), getSlot()
    // -------------------------------------------------------------------------

    @Test
    void chainWalksIsA() {
        var spec = new Spec("canonical_id");
        var title = spec.addClass("Title", ClassKind.ABSTRACT, null, null, null);
        var movie = spec.addClass("Movie", ClassKind.CONCRETE, title, null, null);
        var names = movie.chain().stream().map(OntologyClass::name).toList();
        assertThat(names).containsExactly("Movie", "Title");
    }

    @Test
    void getSlotWalksInheritance() {
        var spec = new Spec("canonical_id");
        var title = spec.addClass("Title", ClassKind.ABSTRACT, null, null, null);
        title.slot("name", Primitive.TEXT, true, false, null);
        var movie = spec.addClass("Movie", ClassKind.CONCRETE, title, null, null);
        var s = movie.getSlot("name");
        assertThat(s.type()).isEqualTo(Primitive.TEXT);
        assertThat(s.required()).isTrue();
    }

    @Test
    void getSlotThrowsOnMiss() {
        var spec = new Spec("canonical_id");
        var movie = spec.addClass("Movie");
        assertThatThrownBy(() -> movie.getSlot("nonexistent_slot"))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("nonexistent_slot");
    }

    @Test
    void effectiveSlotsDedupeFirstSeen() {
        var spec = new Spec("canonical_id");
        var parent = spec.addClass("Parent");
        parent.slot("name", Primitive.TEXT);
        var child = spec.addClass("Child", ClassKind.CONCRETE, parent, null, null);
        child.slot("name", Primitive.INTEGER); // shadows parent
        var names = child.effectiveSlots().stream().map(Slot::name).toList();
        // child's "name" first (own), then inherited canonical_id
        assertThat(names).contains("name", "canonical_id");
        var nameSlot = child.effectiveSlots().stream().filter(s -> s.name().equals("name")).findFirst().orElseThrow();
        assertThat(nameSlot.type()).isEqualTo(Primitive.INTEGER);
    }

    @Test
    void identifierSlotWalksInheritance() {
        var spec = new Spec("canonical_id");
        var title = spec.addClass("Title", ClassKind.ABSTRACT, null, null, null);
        var movie = spec.addClass("Movie", ClassKind.CONCRETE, title, null, null);
        // canonical_id is on Title (abstract); Movie inherits it
        assertThat(movie.identifierSlot().name()).isEqualTo("canonical_id");
    }

    // -------------------------------------------------------------------------
    // Name accessors — require spec
    // -------------------------------------------------------------------------

    @Test
    void bindingsTableNameUsesSchemaAndLowerCaseName() {
        var spec = new Spec("canonical_id");
        var movie = spec.addClass("Movie");
        assertThat(movie.bindingsTableName()).isEqualTo("knot_data.movie_bindings");
    }

    @Test
    void resolvedViewNameUsesSchemaAndLowerCaseName() {
        var spec = new Spec("canonical_id");
        var movie = spec.addClass("Movie");
        assertThat(movie.resolvedViewName()).isEqualTo("knot_data.movie_resolved");
    }

    @Test
    void nameAccessorsTrackSpecSchema() {
        var spec = new Spec("canonical_id", "custom_schema");
        var movie = spec.addClass("Movie");
        assertThat(movie.bindingsTableName()).isEqualTo("custom_schema.movie_bindings");
        assertThat(movie.resolvedViewName()).isEqualTo("custom_schema.movie_resolved");
        assertThat(movie.allSourcesViewName()).isEqualTo("custom_schema.movie_all_sources");
    }

    @Test
    void nameAccessorsThrowWhenNotAttachedToSpec() {
        var cls = new OntologyClass("Loose");
        assertThatThrownBy(cls::bindingsTableName)
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("not attached to a Spec");
    }

    // -------------------------------------------------------------------------
    // referrers() and back()
    // -------------------------------------------------------------------------

    @Test
    void referrersFindsIncomingFkSlots() {
        var spec = new Spec("canonical_id");
        var person = spec.addClass("Person");
        var movie = spec.addClass("Movie");
        movie.slot("director", person);
        var referrers = person.referrers();
        assertThat(referrers).hasSize(1);
        assertThat(referrers.get(0).cls()).isSameAs(movie);
        assertThat(referrers.get(0).slot().name()).isEqualTo("director");
    }

    @Test
    void backReturnsReverseRef() {
        var spec = new Spec("canonical_id");
        var person = spec.addClass("Person");
        var movie = spec.addClass("Movie");
        movie.slot("director", person);
        var ref = person.back(movie, "director");
        assertThat(ref.primaryCls()).isSameAs(person);
        assertThat(ref.otherCls()).isSameAs(movie);
        assertThat(ref.fkSlotName()).isEqualTo("director");
    }

    @Test
    void backThrowsOnUnknownFk() {
        var spec = new Spec("canonical_id");
        var person = spec.addClass("Person");
        var movie = spec.addClass("Movie");
        assertThatThrownBy(() -> person.back(movie, "nonexistent"))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("no FK");
    }

    // -------------------------------------------------------------------------
    // Query entry points
    // -------------------------------------------------------------------------

    @Test
    void resolvedReturnsQueryWithResolvedLayer() {
        var spec = new Spec("canonical_id");
        var movie = spec.addClass("Movie");
        var q = movie.resolved();
        assertThat(q.layer()).isEqualTo(Layer.RESOLVED);
        assertThat(q.className()).isEqualTo("Movie");
    }

    @Test
    void queryEntryPointsThrowForAbstractClass() {
        var spec = new Spec("canonical_id");
        var title = spec.addClass("Title", ClassKind.ABSTRACT, null, null, null);
        assertThatThrownBy(title::resolved)
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("abstract");
    }

    // -------------------------------------------------------------------------
    // col() and bindingsCol()
    // -------------------------------------------------------------------------

    @Test
    void colRejectsUnknownSlotAtConstructionTime() {
        var spec = new Spec("canonical_id");
        var movie = spec.addClass("Movie");
        movie.slot("year", Primitive.INTEGER);
        assertThatThrownBy(() -> movie.col().get("nonexistent_slot"))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("nonexistent_slot");
    }

    @Test
    void colResolvesInheritedSlot() {
        var spec = new Spec("canonical_id");
        var title = spec.addClass("Title", ClassKind.ABSTRACT, null, null, null);
        title.slot("name", Primitive.TEXT, true, false, null);
        var movie = spec.addClass("Movie", ClassKind.CONCRETE, title, null, null);
        var ref = movie.col().get("name");
        assertThat(ref).isNotNull();
    }
}
