package knot.ast.types;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.util.List;
import org.junit.jupiter.api.Test;

class EnumTest {

    @Test
    void ofFactoryMirrorsPythonVarargsConvenience() {
        var e = Enum.of("director", "writer", "actor");
        assertThat(e.values()).containsExactly("director", "writer", "actor");
    }

    @Test
    void rejectsEmptyValues() {
        assertThatThrownBy(() -> Enum.of())
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("ENUM requires at least one value");
        assertThatThrownBy(() -> new Enum(List.of()))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("ENUM requires at least one value");
    }

    @Test
    void rejectsNullValuesList() {
        assertThatThrownBy(() -> new Enum(null))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("ENUM requires at least one value");
    }

    @Test
    void rejectsDuplicateValues() {
        assertThatThrownBy(() -> Enum.of("a", "b", "a", "c", "b"))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("duplicates");
    }

    @Test
    void valuesIsEffectivelyImmutable() {
        var src = new java.util.ArrayList<>(List.of("a", "b"));
        var e = new Enum(src);
        src.add("c"); // mutating source must not affect the record
        assertThat(e.values()).containsExactly("a", "b");
        assertThatThrownBy(() -> e.values().add("z")).isInstanceOf(UnsupportedOperationException.class);
    }

    @Test
    void toStringMirrorsPython() {
        assertThat(Enum.of("a", "b").toString()).isEqualTo("enum('a', 'b')");
    }
}
