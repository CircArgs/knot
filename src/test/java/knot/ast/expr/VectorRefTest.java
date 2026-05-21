package knot.ast.expr;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.util.List;
import org.junit.jupiter.api.Test;

class VectorRefTest {

    private static VectorRef cosine4() {
        return new VectorRef("Movie", "title_embedding", "cosine", 4);
    }

    @Test
    void constructionValidatesClassName() {
        assertThatThrownBy(() -> new VectorRef("", "emb", "cosine", 4))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("className");
    }

    @Test
    void constructionValidatesSlotName() {
        assertThatThrownBy(() -> new VectorRef("Movie", " ", "cosine", 4))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("slotName");
    }

    @Test
    void constructionValidatesMetric() {
        assertThatThrownBy(() -> new VectorRef("Movie", "emb", null, 4))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("metric");
    }

    @Test
    void constructionValidatesDim() {
        assertThatThrownBy(() -> new VectorRef("Movie", "emb", "cosine", 0))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("dim");

        assertThatThrownBy(() -> new VectorRef("Movie", "emb", "cosine", -1))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("dim");
    }

    @Test
    void distanceToLiteralVector() {
        var ref = cosine4();
        var dist = ref.distanceTo(List.of(0.1, 0.2, 0.3, 0.4));
        assertThat(dist).isInstanceOf(VectorDistance.class);
        assertThat(dist.className()).isEqualTo("Movie");
        assertThat(dist.slotName()).isEqualTo("title_embedding");
        assertThat(dist.metric()).isEqualTo("cosine");
        assertThat(dist.dim()).isEqualTo(4);
        assertThat(dist.target()).isInstanceOf(List.class);
    }

    @Test
    void distanceToCrossRowVectorRef() {
        var movieEmb = cosine4();
        var docEmb = new VectorRef("Document", "body_embedding", "cosine", 4);
        var dist = movieEmb.distanceTo(docEmb);
        assertThat(dist.target()).isEqualTo(docEmb);
        assertThat(dist.metric()).isEqualTo("cosine");
    }

    @Test
    void distanceToMetricMismatchThrows() {
        var cosine = cosine4();
        var l2 = new VectorRef("Movie", "emb_l2", "l2", 4);
        assertThatThrownBy(() -> cosine.distanceTo(l2))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("metric mismatch");
    }

    @Test
    void distanceToDimMismatchThrows() {
        var dim4 = cosine4();
        var dim8 = new VectorRef("Movie", "emb8", "cosine", 8);
        assertThatThrownBy(() -> dim4.distanceTo(dim8))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("dim mismatch");
    }

    @Test
    void distanceToInvalidTargetTypeThrows() {
        var ref = cosine4();
        assertThatThrownBy(() -> ref.distanceTo("not a vector"))
                .isInstanceOf(IllegalArgumentException.class);
    }

    @Test
    void implementsValueExpr() {
        assertThat(cosine4()).isInstanceOf(ValueExpr.class);
        assertThat(cosine4()).isInstanceOf(Expr.class);
    }
}
