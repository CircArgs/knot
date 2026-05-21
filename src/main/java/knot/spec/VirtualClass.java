package knot.spec;

import knot.ast.expr.BoolOp;
import knot.ast.expr.Expr;
import knot.ast.select.Query;

/**
 * A virtual class — materialized as a SQL view over an is_a parent table,
 * rows selected by the {@code definition} predicate. The definition is an
 * {@link Expr} produced by the semantic builder, not raw SQL.
 *
 * <p>{@code isA} may be an {@link OntologyClass} (rooted directly in a
 * concrete class's resolved view) or another {@link VirtualClass} (nested
 * virtual — the view filters from the parent virtual's view). Python uses
 * a duck-typed union; Java models it as {@code Object} with explicit
 * {@code instanceof} checks at the boundary.
 *
 * <p>Mirrors the Python {@code @dataclass(slots=True) VirtualClass}.
 */
public final class VirtualClass {

    private final String name;
    private final Object isA; // OntologyClass | VirtualClass
    private final Expr definition;
    private final String description;

    public VirtualClass(String name, Object isA, Expr definition, String description) {
        Names.checkName("VirtualClass", name);
        if (isA == null) {
            throw new IllegalArgumentException(
                    "VirtualClass '" + name + "'.isA must be non-null");
        }
        if (!(isA instanceof OntologyClass) && !(isA instanceof VirtualClass)) {
            throw new IllegalArgumentException(
                    "VirtualClass '" + name + "'.isA must be an OntologyClass or VirtualClass; got "
                            + isA.getClass().getSimpleName());
        }
        if (definition == null) {
            throw new IllegalArgumentException(
                    "VirtualClass '" + name + "'.definition must be an Expr "
                            + "(use the correlated-aggregate form: e.g. "
                            + "credit.col(\"movie\").eq(this.get(\"Movie\")).and_(...).any_())");
        }
        this.name = name;
        this.isA = isA;
        this.definition = definition;
        this.description = description;
    }

    public String name() {
        return name;
    }

    /** {@code OntologyClass} or {@code VirtualClass} — check at the boundary. */
    public Object isA() {
        return isA;
    }

    public Expr definition() {
        return definition;
    }

    public String description() {
        return description;
    }

    /**
     * Walk up the {@code isA} chain and return the concrete
     * {@link OntologyClass} at the base of this virtual's lineage.
     */
    public OntologyClass concreteRoot() {
        Object node = isA;
        while (node instanceof VirtualClass vc) {
            node = vc.isA;
        }
        return (OntologyClass) node;
    }

    /**
     * Return the {@link Spec} this virtual belongs to by walking up to the
     * concrete root (which carries the back-reference). {@code null} when
     * the virtual hasn't been registered yet.
     */
    Spec specRef() {
        return concreteRoot().specOrNull();
    }

    /**
     * Define a virtual subclass of this virtual class — rows that satisfy
     * both this virtual's definition AND {@code where}. Materialized as a
     * SQL view filtering from this virtual's own view.
     */
    public VirtualClass addVirtual(String name, Expr where, String description) {
        var spec = specRef();
        if (spec == null) {
            throw new IllegalStateException(
                    "VirtualClass '" + this.name + "' not attached to a Spec — "
                            + "create via cls.addVirtual() rather than constructing directly");
        }
        if (spec.classes().containsKey(name)) {
            throw new IllegalArgumentException("Spec already has a class named '" + name + "'");
        }
        var vc = new VirtualClass(name, this, where, description);
        spec.registerClass(name, vc);
        return vc;
    }

    /** Overload of {@link #addVirtual(String, Expr, String)} without a description. */
    public VirtualClass addVirtual(String name, Expr where) {
        return addVirtual(name, where, null);
    }

    /**
     * AND every ancestor virtual's predicate together — the same WHERE body
     * the DDL emits for this virtual's view.
     */
    public Expr combinedPredicate() {
        // Walk up the chain collecting predicates self→...→last-virtual.
        var predicates = new java.util.ArrayList<Expr>();
        Object cur = this;
        while (cur instanceof VirtualClass vc) {
            predicates.add(vc.definition);
            cur = vc.isA;
        }
        var combined = predicates.get(0);
        for (int i = 1; i < predicates.size(); i++) {
            combined = new BoolOp("AND", combined, predicates.get(i));
        }
        return combined;
    }

    /**
     * Query the virtual class. Returns a {@link Query} against the
     * concrete root's resolved view filtered by this virtual's combined
     * predicate chain.
     */
    public Query resolved() {
        return concreteRoot().resolved().where(combinedPredicate());
    }
}
