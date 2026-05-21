package knot.ast.select;

import static org.assertj.core.api.Assertions.assertThat;

import org.junit.jupiter.api.Test;

class LayerTest {

    @Test
    void suffixMatchesPythonStrEnumValue() {
        assertThat(Layer.RESOLVED.suffix()).isEqualTo("_resolved");
        assertThat(Layer.ALL_SOURCES.suffix()).isEqualTo("_all_sources");
        assertThat(Layer.BINDINGS.suffix()).isEqualTo("_bindings");
        assertThat(Layer.CANONICAL.suffix()).isEmpty();
    }
}
