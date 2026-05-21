package knot.ast.select;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import knot.ast.expr.Ref;
import org.junit.jupiter.api.Test;

class OrderByTest {

    private static Ref yearRef() {
        return new Ref("Movie", "year");
    }

    @Test
    void defaultDirectionIsAsc() {
        var ob = new OrderBy(yearRef());
        assertThat(ob.direction()).isEqualTo("asc");
    }

    @Test
    void descDirectionAccepted() {
        var ob = new OrderBy(yearRef(), "desc");
        assertThat(ob.direction()).isEqualTo("desc");
    }

    @Test
    void invalidDirectionThrows() {
        assertThatThrownBy(() -> new OrderBy(yearRef(), "ASCENDING"))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("'asc' or 'desc'");
    }

    @Test
    void nullDirectionThrows() {
        assertThatThrownBy(() -> new OrderBy(yearRef(), null))
                .isInstanceOf(IllegalArgumentException.class);
    }

    @Test
    void nullRefThrows() {
        assertThatThrownBy(() -> new OrderBy(null, "asc"))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("ref");
    }

    @Test
    void refIsStoredCorrectly() {
        var ref = yearRef();
        var ob = new OrderBy(ref, "desc");
        assertThat(ob.ref()).isEqualTo(ref);
    }
}
