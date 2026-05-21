package knot.ast.expr;

import static knot.ast.expr.Expressions.*;
import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import org.junit.jupiter.api.Test;

class AggExprTest {

    @Test
    void countNoArgIsCountStar() {
        var expr = count();
        assertThat(expr).isInstanceOf(AggExpr.class);
        assertThat(expr.kind()).isEqualTo("count");
        assertThat(expr.expr()).isNull();
        assertThat(expr.filterPredicate()).isNull();
    }

    @Test
    void countWithArgIsTyped() {
        var ref = new Ref("Movie", "year");
        var expr = count(ref);
        assertThat(expr.kind()).isEqualTo("count");
        assertThat(expr.expr()).isEqualTo(ref);
    }

    @Test
    void sumRequiresExpr() {
        var ref = new Ref("Movie", "year");
        var expr = sum_(ref);
        assertThat(expr.kind()).isEqualTo("sum");
        assertThat(expr.expr()).isEqualTo(ref);
    }

    @Test
    void avgRequiresExpr() {
        var ref = new Ref("Movie", "year");
        var expr = avg(ref);
        assertThat(expr.kind()).isEqualTo("avg");
        assertThat(expr.expr()).isEqualTo(ref);
    }

    @Test
    void minRequiresExpr() {
        var ref = new Ref("Movie", "year");
        var expr = min_(ref);
        assertThat(expr.kind()).isEqualTo("min");
        assertThat(expr.expr()).isEqualTo(ref);
    }

    @Test
    void maxRequiresExpr() {
        var ref = new Ref("Movie", "year");
        var expr = max_(ref);
        assertThat(expr.kind()).isEqualTo("max");
        assertThat(expr.expr()).isEqualTo(ref);
    }

    @Test
    void nonCountKindWithNullExprThrows() {
        assertThatThrownBy(() -> new AggExpr("sum", null, null))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("requires an inner Expr");
    }

    @Test
    void invalidKindThrows() {
        assertThatThrownBy(() -> new AggExpr("median", null, null))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("kind must be one of");
    }

    @Test
    void filterReturnsNewExprWithPredicate() {
        var ref = new Ref("Credit", "role");
        var base = count(new Ref("Credit", "canonical_id"));
        var filtered = base.filter(new Compare("=", ref, new Literal("director")));
        // original unmodified
        assertThat(base.filterPredicate()).isNull();
        // new copy has predicate
        assertThat(filtered.filterPredicate()).isNotNull();
        assertThat(filtered.kind()).isEqualTo("count");
        assertThat(filtered.expr()).isEqualTo(base.expr());
    }

    @Test
    void filterWithNullPredicateThrows() {
        var base = count();
        assertThatThrownBy(() -> base.filter(null))
                .isInstanceOf(IllegalArgumentException.class);
    }

    @Test
    void aggExprImplementsValueExpr() {
        // AggExpr implements ValueExpr so comparison operators work.
        var expr = count();
        assertThat(expr).isInstanceOf(ValueExpr.class);
        assertThat(expr).isInstanceOf(Expr.class);
    }
}
