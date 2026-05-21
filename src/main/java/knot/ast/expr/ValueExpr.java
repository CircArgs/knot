package knot.ast.expr;

import java.util.List;

/**
 * Mixin for {@link Expr} nodes that produce a SQL value (slot ref, literal,
 * count-of-relation, aggregate). Defines comparison + null + range + IN
 * operators that each produce a boolean {@link Expr}.
 *
 * <p>The Python source uses operator overloading ({@code __gt__},
 * {@code __eq__}, etc.). Java has no operator overloading; the API surface is
 * explicit methods. Names mirror Python convention where possible
 * ({@code eq}, {@code ne}, {@code lt}, {@code le}, {@code gt}, {@code ge},
 * {@code in_}, {@code notIn}, {@code isNull}, {@code isNotNull},
 * {@code between}).
 *
 * <p>Java records auto-generate {@link Object#equals(Object) equals} based on
 * component equality — the methods on this interface are deliberately named
 * differently from {@code equals} so the AST builder shape doesn't collide
 * with structural equality.
 */
public sealed interface ValueExpr extends Expr
        permits Ref, FkRef, FkChainRef, VectorRef, VectorDistance, Literal,
                CountRel, Aggregate, AggExpr, This {

    /** {@code self = other} — equality comparison (AST node, not Java equality). */
    default Compare eq(Object other) {
        return new Compare("=", this, Expressions.asExpr(other));
    }

    /** {@code self <> other} — inequality comparison. */
    default Compare ne(Object other) {
        return new Compare("<>", this, Expressions.asExpr(other));
    }

    /** {@code self > other}. */
    default Compare gt(Object other) {
        return new Compare(">", this, Expressions.asExpr(other));
    }

    /** {@code self >= other}. */
    default Compare ge(Object other) {
        return new Compare(">=", this, Expressions.asExpr(other));
    }

    /** {@code self < other}. */
    default Compare lt(Object other) {
        return new Compare("<", this, Expressions.asExpr(other));
    }

    /** {@code self <= other}. */
    default Compare le(Object other) {
        return new Compare("<=", this, Expressions.asExpr(other));
    }

    /** {@code self IN (...)}. */
    default InList in_(List<?> values) {
        return new InList(this, List.copyOf(values), false);
    }

    /** {@code self NOT IN (...)}. */
    default InList notIn(List<?> values) {
        return new InList(this, List.copyOf(values), true);
    }

    /** {@code self IS NULL}. */
    default IsNull isNull() {
        return new IsNull(this, false);
    }

    /** {@code self IS NOT NULL}. */
    default IsNull isNotNull() {
        return new IsNull(this, true);
    }

    /** {@code self BETWEEN low AND high}. */
    default Between between(Object low, Object high) {
        return new Between(this, low, high);
    }
}
