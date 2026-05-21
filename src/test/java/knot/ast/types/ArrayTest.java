package knot.ast.types;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import org.junit.jupiter.api.Test;

class ArrayTest {

    @Test
    void wrapsAnyTypeExpression() {
        var a = new Array(Primitive.TEXT);
        assertThat(a.of()).isEqualTo(Primitive.TEXT);
    }

    @Test
    void rejectsNullElementType() {
        assertThatThrownBy(() -> new Array(null))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("Array.of");
    }

    @Test
    void toStringMirrorsPython() {
        assertThat(new Array(Primitive.INTEGER).toString()).isEqualTo("array<integer>");
    }

    @Test
    void nestsRecursively() {
        var nested = new Array(new Array(Primitive.TEXT));
        assertThat(nested.toString()).isEqualTo("array<array<text>>");
    }
}
