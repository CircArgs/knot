package knot.ast.expr;

import static knot.ast.expr.Expressions.*;
import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.util.ArrayList;
import java.util.List;
import org.junit.jupiter.api.Test;

class TupleInTest {

    private static Ref ref(String slot) {
        return new Ref("Movie", slot);
    }

    @Test
    void arityMismatchThrows() {
        // 1 left column, but value row has 2 elements
        assertThatThrownBy(
                        () -> new TupleIn(
                                List.of(ref("year")),
                                List.of(List.of(2020, "extra")),
                                false))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("arity mismatch");
    }

    @Test
    void emptyLeftsThrows() {
        assertThatThrownBy(() -> new TupleIn(List.of(), List.of(), false))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("at least one");
    }

    @Test
    void tupleInFactory() {
        var node = tupleIn(
                List.of(ref("year"), ref("title")),
                List.of(List.of(2020, "Tenet"), List.of(1994, "Pulp Fiction")));
        assertThat(node.negated()).isFalse();
        assertThat(node.lefts()).hasSize(2);
        assertThat(node.values()).hasSize(2);
        assertThat(node.values().get(0)).containsExactly(2020, "Tenet");
        assertThat(node.values().get(1)).containsExactly(1994, "Pulp Fiction");
    }

    @Test
    void tupleNotInFactory() {
        var node = tupleNotIn(
                List.of(ref("year")),
                List.of(List.of(1999), List.of(2000)));
        assertThat(node.negated()).isTrue();
        assertThat(node.values()).hasSize(2);
    }

    @Test
    void emptyValuesListAccepted() {
        // No value rows — valid SQL "x IN ()" though postgres rejects it; AST is fine.
        var node = tupleIn(List.of(ref("year")), List.of());
        assertThat(node.values()).isEmpty();
    }

    @Test
    void listsAreImmutable() {
        var node = tupleIn(List.of(ref("year")), List.of(List.of(2020)));
        assertThatThrownBy(() -> node.lefts().add(ref("title")))
                .isInstanceOf(UnsupportedOperationException.class);
        assertThatThrownBy(() -> node.values().add(List.of(1)))
                .isInstanceOf(UnsupportedOperationException.class);
    }

    @Test
    void implementsExpr() {
        var node = tupleIn(List.of(ref("year")), List.of(List.of(2020)));
        assertThat(node).isInstanceOf(Expr.class);
    }

    @Test
    void mutatingSourceListDoesNotAffectNode() {
        var lefts = new ArrayList<Expr>();
        lefts.add(ref("year"));
        var node = tupleIn(lefts, List.of(List.of(2020)));
        lefts.add(ref("title")); // mutate original
        assertThat(node.lefts()).hasSize(1); // node unaffected
    }
}
