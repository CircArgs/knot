package knot.ast.expr;

import static knot.ast.expr.Expressions.*;
import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.util.List;
import org.junit.jupiter.api.Test;

class TupleCompareTest {

    private static Ref ref(String slot) {
        return new Ref("Movie", slot);
    }

    @Test
    void arityMismatchThrows() {
        assertThatThrownBy(
                        () -> new TupleCompare("<", List.of(ref("year")),
                                List.of(new Literal(1), new Literal(2))))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("arity mismatch");
    }

    @Test
    void emptyLeftsThrows() {
        assertThatThrownBy(() -> new TupleCompare("<", List.of(), List.of()))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("at least one");
    }

    @Test
    void badOpThrows() {
        assertThatThrownBy(
                        () -> new TupleCompare("=", List.of(ref("year")),
                                List.of(new Literal(1))))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("lex-order");
    }

    @Test
    void tupleLtSetsCorrectOp() {
        var node = tupleLt(List.of(ref("year")), List.of(2020));
        assertThat(node.op()).isEqualTo("<");
        assertThat(node.lefts()).hasSize(1);
        assertThat(node.rights()).hasSize(1);
        // right is coerced to Literal
        assertThat(node.rights().get(0)).isEqualTo(new Literal(2020));
    }

    @Test
    void tupleLeSetsCorrectOp() {
        var node = tupleLe(List.of(ref("year")), List.of(2020));
        assertThat(node.op()).isEqualTo("<=");
    }

    @Test
    void tupleGtSetsCorrectOp() {
        var node = tupleGt(List.of(ref("year")), List.of(1900));
        assertThat(node.op()).isEqualTo(">");
    }

    @Test
    void tupleGeSetsCorrectOp() {
        var node = tupleGe(List.of(ref("year")), List.of(1900));
        assertThat(node.op()).isEqualTo(">=");
    }

    @Test
    void multiColumnArityMatches() {
        var year = ref("year");
        var title = ref("title");
        var node = tupleLt(List.of(year, title), List.of(2020, "Z"));
        assertThat(node.lefts()).hasSize(2);
        assertThat(node.rights()).hasSize(2);
    }

    @Test
    void listsAreImmutable() {
        var node = tupleLt(List.of(ref("year")), List.of(2020));
        assertThatThrownBy(() -> node.lefts().add(ref("title")))
                .isInstanceOf(UnsupportedOperationException.class);
        assertThatThrownBy(() -> node.rights().add(new Literal(1)))
                .isInstanceOf(UnsupportedOperationException.class);
    }

    @Test
    void implementsExpr() {
        var node = tupleLt(List.of(ref("year")), List.of(2020));
        assertThat(node).isInstanceOf(Expr.class);
    }

    @Test
    void allValidOpsAccepted() {
        for (var op : List.of("<", "<=", ">", ">=")) {
            var node = new TupleCompare(op, List.of(ref("year")), List.of(new Literal(1)));
            assertThat(node.op()).isEqualTo(op);
        }
    }
}
