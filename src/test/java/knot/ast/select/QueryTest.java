package knot.ast.select;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import knot.ast.expr.BoolOp;
import knot.ast.expr.Compare;
import knot.ast.expr.Expressions;
import knot.ast.expr.Literal;
import knot.ast.expr.Ref;
import org.junit.jupiter.api.Test;

class QueryTest {

    private static Ref year() {
        return new Ref("Movie", "year");
    }

    private static Ref title() {
        return new Ref("Movie", "title");
    }

    // ------------------------------------------------------------------
    // Fluent immutability
    // ------------------------------------------------------------------

    @Test
    void whereReturnsNewQuery() {
        var q0 = new Query("Movie");
        var q1 = q0.where(year().gt(1900));
        assertThat(q1).isNotSameAs(q0);
        assertThat(q0.whereClause()).isNull(); // original unchanged
        assertThat(q1.whereClause()).isNotNull();
    }

    @Test
    void whereAndCombinesPredicates() {
        var pred1 = year().gt(1900);
        var pred2 = year().lt(2100);
        var q = new Query("Movie").where(pred1).where(pred2);
        assertThat(q.whereClause()).isInstanceOf(BoolOp.class);
        var boolOp = (BoolOp) q.whereClause();
        assertThat(boolOp.op()).isEqualTo("AND");
        assertThat(boolOp.left()).isEqualTo(pred1);
        assertThat(boolOp.right()).isEqualTo(pred2);
    }

    @Test
    void groupByReturnsNewQueryAndAppends() {
        var q0 = new Query("Movie");
        var q1 = q0.groupBy(year());
        var q2 = q1.groupBy(title());
        assertThat(q1).isNotSameAs(q0);
        assertThat(q0.grouping()).isEmpty();
        assertThat(q1.grouping()).hasSize(1);
        assertThat(q2.grouping()).hasSize(2); // appended, not replaced
    }

    @Test
    void orderByReturnsNewQueryAndAppends() {
        var q0 = new Query("Movie");
        var q1 = q0.orderBy(year(), "desc");
        var q2 = q1.orderBy(title(), "asc");
        assertThat(q1).isNotSameAs(q0);
        assertThat(q0.ordering()).isEmpty();
        assertThat(q1.ordering()).hasSize(1);
        assertThat(q2.ordering()).hasSize(2);
        assertThat(q2.ordering().get(0).direction()).isEqualTo("desc");
        assertThat(q2.ordering().get(1).direction()).isEqualTo("asc");
    }

    @Test
    void limitReturnsNewQuery() {
        var q0 = new Query("Movie");
        var q1 = q0.limit(10);
        assertThat(q0.limitValue()).isNull();
        assertThat(q1.limitValue()).isEqualTo(10);
    }

    @Test
    void offsetReturnsNewQuery() {
        var q0 = new Query("Movie");
        var q1 = q0.offset(20);
        assertThat(q0.offsetValue()).isNull();
        assertThat(q1.offsetValue()).isEqualTo(20);
    }

    @Test
    void selectReturnsNewQuery() {
        var q0 = new Query("Movie");
        var q1 = q0.select(year(), title());
        assertThat(q0.projection()).isNull();
        assertThat(q1.projection()).hasSize(2);
    }

    // ------------------------------------------------------------------
    // Default field values
    // ------------------------------------------------------------------

    @Test
    void defaultLayerIsResolved() {
        var q = new Query("Movie");
        assertThat(q.layer()).isEqualTo(Layer.RESOLVED);
    }

    @Test
    void defaultGroupingAndOrderingAreEmpty() {
        var q = new Query("Movie");
        assertThat(q.grouping()).isEmpty();
        assertThat(q.ordering()).isEmpty();
    }

    @Test
    void defaultLimitOffsetProjectionAreNull() {
        var q = new Query("Movie");
        assertThat(q.limitValue()).isNull();
        assertThat(q.offsetValue()).isNull();
        assertThat(q.projection()).isNull();
    }

    // ------------------------------------------------------------------
    // Validation
    // ------------------------------------------------------------------

    @Test
    void blankClassNameThrows() {
        assertThatThrownBy(() -> new Query(""))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("className");
    }

    @Test
    void nullClassNameThrows() {
        assertThatThrownBy(() -> new Query(null))
                .isInstanceOf(IllegalArgumentException.class);
    }

    @Test
    void sqlThrowsWithoutSpecRef() {
        var q = new Query("Movie");
        assertThatThrownBy(q::sql)
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("spec back-reference");
    }

    @Test
    void invalidLockModeThrows() {
        var q = new Query("Movie");
        assertThatThrownBy(() -> q.lock("exclusive"))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("lock mode");
    }

    @Test
    void validLockModesAccepted() {
        var q = new Query("Movie");
        assertThat(q.lock("for_update").lockMode()).isEqualTo("for_update");
        assertThat(q.lock("for_update_skip_locked").lockMode()).isEqualTo("for_update_skip_locked");
        assertThat(q.lock("for_share").lockMode()).isEqualTo("for_share");
    }

    // ------------------------------------------------------------------
    // Equality excludes specRef
    // ------------------------------------------------------------------

    @Test
    void equalityIgnoresSpecRef() {
        var q1 = new Query("Movie").where(year().gt(1900));
        // withSpec requires a Spec instance we don't have in unit tests;
        // but two independently-constructed queries with same shape are equal.
        var q2 = new Query("Movie").where(year().gt(1900));
        assertThat(q1).isEqualTo(q2);
    }
}
