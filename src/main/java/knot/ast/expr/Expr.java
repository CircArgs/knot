package knot.ast.expr;

/**
 * Root of the expression AST tree.
 *
 * <p>Pure data — every implementor is a {@code record}. Rendering lives in
 * {@code knot.compile.expr} (a pattern-matching switch over this sealed hierarchy
 * replaces the Python {@code @functools.singledispatch} table).
 *
 * <p>Boolean combinators are exposed as default methods so any node can be
 * AND/OR/NOT-ed without callers having to reach for a builder. Set-aggregate
 * primitives ({@link #any_()}, {@link #none_()}, {@link #all_(Expr)},
 * {@link #count()}) treat {@code this} as a predicate over an implicit row-set;
 * the primary-class inference happens at compile time.
 *
 * <p>Method names use suffix-underscores ({@code and_}, {@code or_},
 * {@code not_}, {@code any_}, {@code none_}, {@code all_}) where the natural
 * spelling would collide with a Java keyword or the {@link Object#equals(Object)}
 * /{@link Object#hashCode()} contract. The Python source uses operator
 * overloading ({@code __and__}, {@code __or__}, {@code __invert__}); Java has
 * no operator overloading so the API surface is explicit methods.
 */
public sealed interface Expr
        permits Ref, FkRef, FkChainRef, VectorRef, VectorDistance, TargetExists,
                Literal, Compare, BoolOp, Not, IsNull, InList, Between, Exists,
                CountRel, Aggregate, AggExpr, Raw, This, TupleCompare, TupleIn {

    /** {@code self AND other} — boolean conjunction. */
    default BoolOp and_(Expr other) {
        return new BoolOp("AND", this, Expressions.asExpr(other));
    }

    /** {@code self OR other} — boolean disjunction. */
    default BoolOp or_(Expr other) {
        return new BoolOp("OR", this, Expressions.asExpr(other));
    }

    /** {@code NOT self} — boolean negation. */
    default Not not_() {
        return new Not(this);
    }

    /** {@code EXISTS (SELECT 1 FROM … WHERE self)} — at least one row. */
    default Aggregate any_() {
        return new Aggregate("any", this, null);
    }

    /** {@code NOT EXISTS (…)} — no row satisfies {@code self}. */
    default Aggregate none_() {
        return new Aggregate("none", this, null);
    }

    /**
     * {@code NOT EXISTS (… AND NOT condition)} — every row in the implicit
     * set defined by {@code self} also satisfies {@code condition}.
     * Vacuously true on the empty set (math-correct default).
     */
    default Aggregate all_(Expr condition) {
        return new Aggregate("all", this, condition);
    }

    /**
     * {@code (SELECT COUNT(*) FROM … WHERE self)} — value-expression,
     * comparable: {@code predicate.count().gt(5)}.
     */
    default Aggregate count() {
        return new Aggregate("count", this, null);
    }
}
