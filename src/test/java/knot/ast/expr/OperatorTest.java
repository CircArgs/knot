package knot.ast.expr;

import static knot.ast.expr.Expressions.*;
import static org.assertj.core.api.Assertions.assertThat;

import java.util.List;
import org.junit.jupiter.api.Test;

/**
 * Verifies that ValueExpr / Expr operator methods produce the right AST node types
 * and carry the correct fields. No SQL rendering — pure AST shape checks.
 */
class OperatorTest {

    private static Ref year() {
        return new Ref("Movie", "year");
    }

    // ------------------------------------------------------------------
    // ValueExpr comparison operators
    // ------------------------------------------------------------------

    @Test
    void eqProducesCompare() {
        var node = year().eq(2020);
        assertThat(node).isInstanceOf(Compare.class);
        assertThat(node.op()).isEqualTo("=");
        assertThat(node.left()).isEqualTo(year());
        assertThat(node.right()).isEqualTo(lit(2020));
    }

    @Test
    void neProducesCompare() {
        var node = year().ne(2020);
        assertThat(node.op()).isEqualTo("<>");
    }

    @Test
    void gtProducesCompare() {
        var node = year().gt(1900);
        assertThat(node.op()).isEqualTo(">");
    }

    @Test
    void ltProducesCompare() {
        var node = year().lt(2025);
        assertThat(node.op()).isEqualTo("<");
    }

    @Test
    void geProducesCompare() {
        var node = year().ge(1888);
        assertThat(node.op()).isEqualTo(">=");
    }

    @Test
    void leProducesCompare() {
        var node = year().le(2099);
        assertThat(node.op()).isEqualTo("<=");
    }

    // ------------------------------------------------------------------
    // Boolean combinators (on Expr)
    // ------------------------------------------------------------------

    @Test
    void and_ProducesBoolOp() {
        var left = year().gt(1900);
        var right = year().lt(2025);
        var node = left.and_(right);
        assertThat(node).isInstanceOf(BoolOp.class);
        assertThat(node.op()).isEqualTo("AND");
        assertThat(node.left()).isEqualTo(left);
        assertThat(node.right()).isEqualTo(right);
    }

    @Test
    void or_ProducesBoolOp() {
        var left = year().lt(1900);
        var right = year().gt(2100);
        var node = left.or_(right);
        assertThat(node.op()).isEqualTo("OR");
    }

    @Test
    void not_ProducesNot() {
        var inner = year().gt(1900);
        var node = inner.not_();
        assertThat(node).isInstanceOf(Not.class);
        assertThat(node.expr()).isEqualTo(inner);
    }

    // ------------------------------------------------------------------
    // IN / NOT IN
    // ------------------------------------------------------------------

    @Test
    void in_ProducesInListNotNegated() {
        var node = year().in_(List.of(2019, 2020, 2021));
        assertThat(node).isInstanceOf(InList.class);
        assertThat(node.negated()).isFalse();
        assertThat(node.values()).hasSize(3);
    }

    @Test
    void notInProducesInListNegated() {
        var node = year().notIn(List.of(1999, 2000));
        assertThat(node.negated()).isTrue();
    }

    // ------------------------------------------------------------------
    // IS NULL / IS NOT NULL
    // ------------------------------------------------------------------

    @Test
    void isNullProducesIsNull() {
        var node = year().isNull();
        assertThat(node).isInstanceOf(IsNull.class);
        assertThat(node.negated()).isFalse();
    }

    @Test
    void isNotNullProducesIsNullNegated() {
        var node = year().isNotNull();
        assertThat(node.negated()).isTrue();
    }

    // ------------------------------------------------------------------
    // BETWEEN
    // ------------------------------------------------------------------

    @Test
    void betweenProducesBetween() {
        var node = year().between(1888, 2100);
        assertThat(node).isInstanceOf(Between.class);
        assertThat(node.low()).isEqualTo(1888);
        assertThat(node.high()).isEqualTo(2100);
    }

    // ------------------------------------------------------------------
    // Set-aggregate primitives (on Expr)
    // ------------------------------------------------------------------

    @Test
    void any_ProducesAggregateKindAny() {
        var predicate = year().gt(1900);
        var node = predicate.any_();
        assertThat(node).isInstanceOf(Aggregate.class);
        assertThat(node.kind()).isEqualTo("any");
        assertThat(node.predicate()).isEqualTo(predicate);
        assertThat(node.condition()).isNull();
    }

    @Test
    void none_ProducesAggregateKindNone() {
        var predicate = year().lt(1888);
        var node = predicate.none_();
        assertThat(node.kind()).isEqualTo("none");
    }

    @Test
    void all_ProducesAggregateKindAll() {
        var predicate = year().gt(1900);
        var condition = year().lt(2100);
        var node = predicate.all_(condition);
        assertThat(node.kind()).isEqualTo("all");
        assertThat(node.condition()).isEqualTo(condition);
    }

    @Test
    void countOnExprProducesAggregateKindCount() {
        var predicate = year().gt(1900);
        var node = predicate.count();
        assertThat(node.kind()).isEqualTo("count");
    }

    // ------------------------------------------------------------------
    // Expressions factory helpers
    // ------------------------------------------------------------------

    @Test
    void litWrapsValue() {
        var node = lit(42);
        assertThat(node).isInstanceOf(Literal.class);
        assertThat(node.value()).isEqualTo(42);
    }

    @Test
    void rawWrapsString() {
        var node = raw("NOW()");
        assertThat(node).isInstanceOf(Raw.class);
        assertThat(node.sql()).isEqualTo("NOW()");
    }

    @Test
    void countFactoryNoArgIsCountStar() {
        var node = count();
        assertThat(node.kind()).isEqualTo("count");
        assertThat(node.expr()).isNull();
    }
}
