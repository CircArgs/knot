package knot.ast.types;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import org.junit.jupiter.api.Test;

class VectorTest {

    @Test
    void acceptsAllSupportedMetrics() {
        assertThat(new Vector(384, "cosine").hnswOps()).isEqualTo("vector_cosine_ops");
        assertThat(new Vector(384, "l2").hnswOps()).isEqualTo("vector_l2_ops");
        assertThat(new Vector(384, "ip").hnswOps()).isEqualTo("vector_ip_ops");
    }

    @Test
    void rejectsZeroDim() {
        assertThatThrownBy(() -> new Vector(0, "cosine"))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("VECTOR dim must be a positive integer");
    }

    @Test
    void rejectsNegativeDim() {
        assertThatThrownBy(() -> new Vector(-1, "cosine"))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("VECTOR dim must be a positive integer");
    }

    @Test
    void rejectsUnknownMetric() {
        assertThatThrownBy(() -> new Vector(384, "manhattan"))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("VECTOR metric must be one of");
    }

    @Test
    void rejectsNullMetric() {
        assertThatThrownBy(() -> new Vector(384, null))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("VECTOR metric");
    }

    @Test
    void toStringMirrorsPython() {
        assertThat(new Vector(384, "cosine").toString()).isEqualTo("vector<384,cosine>");
    }
}
