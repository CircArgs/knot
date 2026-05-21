package knot.ast.select;

import static org.assertj.core.api.Assertions.assertThat;

import org.junit.jupiter.api.Test;

class LayerTest {

    @Test
    void resolvedSuffixIsUnderscoredResolved() {
        assertThat(Layer.RESOLVED.suffix()).isEqualTo("_resolved");
    }

    @Test
    void allSourcesSuffixIsUnderscoredAllSources() {
        assertThat(Layer.ALL_SOURCES.suffix()).isEqualTo("_all_sources");
    }

    @Test
    void bindingsSuffixIsUnderscoredBindings() {
        assertThat(Layer.BINDINGS.suffix()).isEqualTo("_bindings");
    }

    @Test
    void canonicalSuffixIsEmpty() {
        assertThat(Layer.CANONICAL.suffix()).isEqualTo("");
    }

    @Test
    void allLayersDefined() {
        assertThat(Layer.values()).hasSize(4);
    }
}
