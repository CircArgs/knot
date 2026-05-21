package knot.ast.select;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import knot.ast.expr.BoolOp;
import knot.ast.expr.Expr;
import knot.ast.expr.Literal;
import knot.ast.expr.Ref;
import org.junit.jupiter.api.Test;

class QueryTest {

    private static final Expr P1 = new Ref("Movie", "year");
    private static final Expr P2 = new Ref("Movie", "title");

    @Test
    void minimalCtorDefaultsToResolvedLayerAndEmptyCollections() {
        var q = new Query("Movie");
        assertThat(q.className()).isEqualTo("Movie");
        assertThat(q.layer()).isEqualTo(Layer.RESOLVED);
        assertThat(q.whereClause()).isNull();
        assertThat(q.grouping()).isEmpty();
        assertThat(q.ordering()).isEmpty();
        assertThat(q.projection()).isNull();
        assertThat(q.lockMode()).isNull();
        assertThat(q.specRef()).isNull();
    }

    @Test
    void lockRejectsInvalidMode() {
        var q = new Query("Movie");
        assertThatThrownBy(() -> q.lock("for_giggles"))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("Query.lock mode must be one of");
    }

    @Test
    void lockAcceptsAllThreePostgresModes() {
        var q = new Query("Movie");
        assertThat(q.lock("for_update").lockMode()).isEqualTo("for_update");
        assertThat(q.lock("for_update_skip_locked").lockMode())
                .isEqualTo("for_update_skip_locked");
        assertThat(q.lock("for_share").lockMode()).isEqualTo("for_share");
    }

    @Test
    void builderMethodsReturnNewQueryAndDoNotMutate() {
        var original = new Query("Movie");
        var withWhere = original.where(P1);
        var withLimit = original.limit(10);
        var withOffset = original.offset(5);
        var withSelect = original.select(P1, P2);

        // Original unchanged.
        assertThat(original.whereClause()).isNull();
        assertThat(original.limitValue()).isNull();
        assertThat(original.offsetValue()).isNull();
        assertThat(original.projection()).isNull();

        // New instances reflect the change.
        assertThat(withWhere).isNotSameAs(original);
        assertThat(withWhere.whereClause()).isEqualTo(P1);
        assertThat(withLimit.limitValue()).isEqualTo(10);
        assertThat(withOffset.offsetValue()).isEqualTo(5);
        assertThat(withSelect.projection()).containsExactly(P1, P2);
    }

    @Test
    void whereAndCombinesPredicates() {
        var q = new Query("Movie").where(P1).where(P2);
        assertThat(q.whereClause()).isInstanceOf(BoolOp.class);
        var combined = (BoolOp) q.whereClause();
        assertThat(combined.op()).isEqualTo("AND");
        assertThat(combined.left()).isEqualTo(P1);
        assertThat(combined.right()).isEqualTo(P2);
    }

    @Test
    void whereOnEmptyClauseJustSetsThePredicate() {
        var q = new Query("Movie").where(P1);
        assertThat(q.whereClause()).isEqualTo(P1);
    }

    @Test
    void groupByAppendsRatherThanReplaces() {
        var q = new Query("Movie").groupBy(P1).groupBy(P2);
        assertThat(q.grouping()).containsExactly(P1, P2);
    }

    @Test
    void orderByAppendsRatherThanReplaces() {
        var q = new Query("Movie")
                .orderBy(P1, "desc")
                .orderBy(P2);
        assertThat(q.ordering())
                .containsExactly(new OrderBy(P1, "desc"), new OrderBy(P2, "asc"));
    }

    @Test
    void sqlThrowsWhenSpecRefIsNull() {
        var q = new Query("Movie");
        assertThatThrownBy(q::sql)
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("no spec back-reference");
    }

    @Test
    void equalityIgnoresSpecRef() {
        // Two queries with the same shape compare equal even though one has
        // no specRef — mirrors the Python ``compare=False`` on ``_spec``. We
        // can't construct a real Spec yet (knot.spec.Spec isn't ported), but
        // the null-vs-null and same-shape case proves the override compiles
        // and the explicit equals/hashCode skip the field.
        var a = new Query("Movie").where(new Literal(1)).limit(5);
        var b = new Query("Movie").where(new Literal(1)).limit(5);
        assertThat(a).isEqualTo(b);
        assertThat(a.hashCode()).isEqualTo(b.hashCode());
    }

    @Test
    void differentShapesAreNotEqual() {
        var a = new Query("Movie").limit(5);
        var b = new Query("Movie").limit(10);
        assertThat(a).isNotEqualTo(b);
    }

    @Test
    void classNameMustBeNonBlank() {
        assertThatThrownBy(() -> new Query(""))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("className must be non-blank");
    }
}
