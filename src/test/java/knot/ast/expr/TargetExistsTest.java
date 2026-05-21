package knot.ast.expr;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import org.junit.jupiter.api.Test;

class TargetExistsTest {

    private static FkRef creditMovie() {
        return new FkRef("Credit", "movie", "Movie");
    }

    @Test
    void fkRefTargetExistsDefaultsToCanonicalId() {
        var node = creditMovie().targetExists();
        assertThat(node).isInstanceOf(TargetExists.class);
        assertThat(node.fkClassName()).isEqualTo("Credit");
        assertThat(node.fkSlotName()).isEqualTo("movie");
        assertThat(node.targetClassName()).isEqualTo("Movie");
        assertThat(node.targetIdentifierSlot()).isEqualTo("canonical_id");
        assertThat(node.negated()).isFalse();
    }

    @Test
    void fkRefTargetExistsCustomIdentifierSlot() {
        var node = creditMovie().targetExists("imdb_id");
        assertThat(node.targetIdentifierSlot()).isEqualTo("imdb_id");
    }

    @Test
    void negateFlipsFlag() {
        var node = creditMovie().targetExists();
        var negated = node.negate();
        assertThat(negated.negated()).isTrue();
        // original unchanged
        assertThat(node.negated()).isFalse();
    }

    @Test
    void doubleNegateRestores() {
        var node = creditMovie().targetExists().negate().negate();
        assertThat(node.negated()).isFalse();
    }

    @Test
    void constructionValidatesFkClassName() {
        assertThatThrownBy(() -> new TargetExists("", "movie", "Movie", "canonical_id", false))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("fkClassName");
    }

    @Test
    void constructionValidatesFkSlotName() {
        assertThatThrownBy(() -> new TargetExists("Credit", " ", "Movie", "canonical_id", false))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("fkSlotName");
    }

    @Test
    void constructionValidatesTargetClassName() {
        assertThatThrownBy(() -> new TargetExists("Credit", "movie", null, "canonical_id", false))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("targetClassName");
    }

    @Test
    void constructionValidatesTargetIdentifierSlot() {
        assertThatThrownBy(() -> new TargetExists("Credit", "movie", "Movie", "", false))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("targetIdentifierSlot");
    }

    @Test
    void implementsExpr() {
        assertThat(creditMovie().targetExists()).isInstanceOf(Expr.class);
    }
}
