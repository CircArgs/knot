package knot.spec;

import knot.ast.expr.CountRel;
import knot.ast.expr.Exists;
import knot.ast.expr.Expr;

/**
 * Navigator for reverse FK traversal: rows on {@code otherCls} whose
 * {@code fkSlotName} column points at {@code primaryCls}.
 *
 * <p>Not directly usable as an {@link Expr} — materialize it into a
 * correlated subquery with {@link #count()}, {@link #any_()}, or
 * {@link #none_()}, optionally filtered first with {@link #where(Expr)}:
 *
 * <pre>{@code
 *   person.back(credit, "person").count().gt(2)
 *   person.back(credit, "person").where(credit.col("role").eq("director")).any_();
 * }</pre>
 *
 * <p>Mirrors the Python {@code @dataclass(slots=True) ReverseRef}. Java
 * uses {@code any_}/{@code none_} since {@code any}/{@code none} would
 * collide with Java's natural spelling conventions in similar AST surfaces.
 */
public record ReverseRef(
        OntologyClass primaryCls,
        OntologyClass otherCls,
        String fkSlotName,
        Expr whereClause) {

    public ReverseRef {
        if (primaryCls == null) {
            throw new IllegalArgumentException("ReverseRef.primaryCls must be non-null");
        }
        if (otherCls == null) {
            throw new IllegalArgumentException("ReverseRef.otherCls must be non-null");
        }
        if (fkSlotName == null || fkSlotName.isEmpty()) {
            throw new IllegalArgumentException(
                    "ReverseRef.fkSlotName must be a non-empty string");
        }
    }

    /** Constructor without a pre-bound where clause. */
    public ReverseRef(OntologyClass primaryCls, OntologyClass otherCls, String fkSlotName) {
        this(primaryCls, otherCls, fkSlotName, null);
    }

    /**
     * Narrow the reverse-FK row-set to those that also satisfy {@code predicate}.
     * Chainable; multiple calls AND together.
     */
    public ReverseRef where(Expr predicate) {
        if (predicate == null) {
            throw new IllegalArgumentException("ReverseRef.where: predicate must be non-null");
        }
        var combined = whereClause == null ? predicate : whereClause.and_(predicate);
        return new ReverseRef(primaryCls, otherCls, fkSlotName, combined);
    }

    /**
     * {@code (SELECT COUNT(*) FROM other WHERE other.fk = primary.id
     * [AND where_clause])}.
     */
    public CountRel count() {
        return new CountRel(
                otherCls.name(),
                fkSlotName,
                primaryCls.name(),
                primaryCls.identifierSlot().name(),
                whereClause);
    }

    /**
     * {@code EXISTS (SELECT 1 FROM other WHERE other.fk = primary.id
     * [AND where_clause])}.
     */
    public Exists any_() {
        return new Exists(
                otherCls.name(),
                fkSlotName,
                primaryCls.name(),
                primaryCls.identifierSlot().name(),
                whereClause,
                false);
    }

    /**
     * {@code NOT EXISTS (SELECT 1 FROM other WHERE other.fk = primary.id
     * [AND where_clause])}.
     */
    public Exists none_() {
        return new Exists(
                otherCls.name(),
                fkSlotName,
                primaryCls.name(),
                primaryCls.identifierSlot().name(),
                whereClause,
                true);
    }
}
