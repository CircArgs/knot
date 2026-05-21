package knot.ast.select;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import knot.ast.expr.Ref;
import org.junit.jupiter.api.Test;

class OrderByTest {

    @Test
    void rejectsInvalidDirection() {
        var ref = new Ref("Movie", "year");
        assertThatThrownBy(() -> new OrderBy(ref, "sideways"))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("must be 'asc' or 'desc'");
    }

    @Test
    void acceptsAscAndDesc() {
        var ref = new Ref("Movie", "year");
        assertThat(new OrderBy(ref, "asc").direction()).isEqualTo("asc");
        assertThat(new OrderBy(ref, "desc").direction()).isEqualTo("desc");
    }

    @Test
    void defaultsDirectionToAsc() {
        var ref = new Ref("Movie", "year");
        assertThat(new OrderBy(ref).direction()).isEqualTo("asc");
    }
}
