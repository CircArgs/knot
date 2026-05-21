package knot.spec;

import java.util.ArrayList;
import java.util.List;
import knot.ast.expr.Expr;
import knot.ast.expr.Raw;
import knot.ast.select.Layer;
import knot.ast.select.Query;
import knot.ast.types.ClassRef;
import knot.compile.Explain;

/**
 * A typed entity class — concrete (has a bindings table + resolved views) or abstract
 * (mixin-only, no table emitted).
 *
 * <p>Mutable by design — slots and the spec back-reference are added after construction via
 * {@link #slot(String, knot.ast.types.TypeExpression)},
 * {@link #slot(String, knot.ast.types.TypeExpression, boolean, boolean, String)}, and the
 * package-private {@link #setSpec(Spec)}.
 *
 * <p>Mirrors the Python {@code @dataclass(slots=True) OntologyClass}.
 */
public final class OntologyClass {

    private final String name;
    private final ClassKind kind;
    private OntologyClass isA;
    private final List<OntologyClass> mixins;
    private final List<Slot> slots;
    private final String description;
    /** Package-private back-reference, set by {@link Spec#addClass}. */
    Spec _spec;

    /**
     * Full constructor. Production code goes through {@link Spec#addClass(String)} or
     * {@link Spec#addClass(String, ClassKind, OntologyClass, List, String)}.
     */
    public OntologyClass(
            String name,
            ClassKind kind,
            OntologyClass isA,
            List<OntologyClass> mixins,
            String description,
            Spec spec) {
        Names.checkName("OntologyClass", name);
        if (kind == null) {
            throw new IllegalArgumentException("OntologyClass.kind must be non-null");
        }
        this.name = name;
        this.kind = kind;
        this.isA = isA;
        this.mixins = new ArrayList<>(mixins != null ? mixins : List.of());
        this.slots = new ArrayList<>();
        this.description = description;
        this._spec = spec;
    }

    /**
     * Bare constructor — {@code kind=CONCRETE}, no is_a, no mixins, no spec.
     * Used by tests that build classes outside a {@link Spec}.
     */
    public OntologyClass(String name) {
        this(name, ClassKind.CONCRETE, null, null, null, null);
    }

    // -------------------------------------------------------------------------
    // Accessors
    // -------------------------------------------------------------------------

    public String name() {
        return name;
    }

    public ClassKind kind() {
        return kind;
    }

    public OntologyClass isA() {
        return isA;
    }

    /** Package-private — only the cycle-injection test path sets this directly. */
    void setIsA(OntologyClass isA) {
        this.isA = isA;
    }

    public List<OntologyClass> mixins() {
        return mixins;
    }

    public List<Slot> slots() {
        return slots;
    }

    public String description() {
        return description;
    }

    /** Package-private — used by {@link Spec#include(Spec)} to re-root on merge. */
    void setSpec(Spec spec) {
        this._spec = spec;
    }

    /** Returns the attached spec, or {@code null} if not yet registered. */
    Spec specOrNull() {
        return _spec;
    }

    // -------------------------------------------------------------------------
    // Slot builder
    // -------------------------------------------------------------------------

    /**
     * Append a slot, validating no duplicate name on this class's own slot list.
     * Returns the created {@link Slot} (fluent — caller may use or discard).
     */
    public Slot slot(String slotName, knot.ast.types.TypeExpression type) {
        return slot(slotName, type, false, false, null);
    }

    /** Full-form slot builder matching the Python keyword-argument signature. */
    public Slot slot(
            String slotName,
            knot.ast.types.TypeExpression type,
            boolean required,
            boolean identifier,
            String description) {
        for (var s : slots) {
            if (s.name().equals(slotName)) {
                throw new IllegalArgumentException(
                        "OntologyClass '" + name + "' already has a slot named '" + slotName + "'");
            }
        }
        var s = new Slot(slotName, type, required, identifier, description);
        slots.add(s);
        return s;
    }

    /**
     * Convenience overload: pass an {@link OntologyClass} directly as the type — creates a
     * {@link ClassRef} FK slot. Mirrors the Python {@code cls.slot("director", person)}.
     */
    public Slot slot(String slotName, OntologyClass target) {
        return slot(slotName, new ClassRef(target), false, false, null);
    }

    /** FK slot with full flags. */
    public Slot slot(
            String slotName, OntologyClass target, boolean required, boolean identifier,
            String description) {
        return slot(slotName, new ClassRef(target), required, identifier, description);
    }

    // -------------------------------------------------------------------------
    // Slot lookup
    // -------------------------------------------------------------------------

    /**
     * Return the slot named {@code name} walking the is_a + mixin chain.
     * Throws {@link IllegalArgumentException} (KeyError equivalent) on miss.
     */
    public Slot getSlot(String slotName) {
        for (var cls : chain()) {
            for (var s : cls.slots) {
                if (s.name().equals(slotName)) {
                    return s;
                }
            }
        }
        throw new IllegalArgumentException("'" + name + "' has no slot '" + slotName + "'");
    }

    /**
     * The first slot up the is_a + mixin chain with {@code identifier=true}.
     * Throws {@link IllegalArgumentException} if none exists.
     */
    public Slot identifierSlot() {
        for (var s : effectiveSlots()) {
            if (s.identifier()) {
                return s;
            }
        }
        throw new IllegalArgumentException("OntologyClass '" + name + "' has no identifier slot");
    }

    /**
     * Self + is_a ancestors + mixins, breadth-first. Mirrors the Python {@code chain()} method.
     */
    public List<OntologyClass> chain() {
        var seen = new ArrayList<OntologyClass>();
        var queue = new ArrayList<OntologyClass>();
        queue.add(this);
        while (!queue.isEmpty()) {
            var cur = queue.remove(0);
            // identity check — same as Python's `any(cur is x for x in seen)`
            boolean alreadySeen = false;
            for (var x : seen) {
                if (x == cur) {
                    alreadySeen = true;
                    break;
                }
            }
            if (alreadySeen) continue;
            seen.add(cur);
            if (cur.isA != null) queue.add(cur.isA);
            queue.addAll(cur.mixins);
        }
        return seen;
    }

    /**
     * Every slot this class effectively has — own + inherited via is_a + mixins.
     * First-seen-wins on name collision (own slots shadow parent slots).
     */
    public List<Slot> effectiveSlots() {
        var seen = new java.util.LinkedHashSet<String>();
        var out = new ArrayList<Slot>();
        for (var cls : chain()) {
            for (var s : cls.slots) {
                if (seen.add(s.name())) {
                    out.add(s);
                }
            }
        }
        return out;
    }

    // -------------------------------------------------------------------------
    // Binding helpers
    // -------------------------------------------------------------------------

    /**
     * All {@link SourceBinding} rows on the owning spec that bind THIS class.
     * Saves callers from filtering {@link Spec#sourceBindings()} by hand.
     */
    public List<SourceBinding> bindings() {
        var spec = requireSpec();
        var out = new ArrayList<SourceBinding>();
        for (var b : spec.sourceBindings()) {
            if (b.ontologyClass() == this) {
                out.add(b);
            }
        }
        return out;
    }

    /**
     * The (at most one) {@link SourceBinding} linking this class to {@code source}.
     * Returns {@code null} if no binding exists.
     */
    public SourceBinding bindingFor(Source source) {
        for (var b : bindings()) {
            if (b.source() == source) {
                return b;
            }
        }
        return null;
    }

    /**
     * Every {@code (OntologyClass, Slot)} pair in the owning spec where the slot's type is a
     * {@link ClassRef} pointing at this class. Used by the ER write path to drive FK fan-out.
     */
    public List<ReferrerEntry> referrers() {
        var spec = requireSpec();
        var out = new ArrayList<ReferrerEntry>();
        for (var other : spec.concreteClasses()) {
            for (var s : other.effectiveSlots()) {
                if (s.type() instanceof ClassRef cr && cr.target() == this) {
                    out.add(new ReferrerEntry(other, s));
                }
            }
        }
        return out;
    }

    /**
     * Return a {@link ReverseRef} navigator for rows on {@code otherCls} whose
     * {@code fkSlotName} column points at this class.
     * Validates at call time that the FK exists — {@link IllegalArgumentException} on typo.
     */
    public ReverseRef back(OntologyClass otherCls, String fkSlotName) {
        for (var entry : referrers()) {
            if (entry.cls() == otherCls && entry.slot().name().equals(fkSlotName)) {
                return new ReverseRef(this, otherCls, fkSlotName);
            }
        }
        var known = referrers().stream()
                .map(e -> "(" + e.cls().name() + ", " + e.slot().name() + ")")
                .toList();
        throw new IllegalArgumentException(
                "no FK from '" + otherCls.name() + "'.'" + fkSlotName + "' → '"
                        + name + "'; known referrers: " + known);
    }

    /** Immutable (class, slot) pair used by {@link #referrers()}. */
    public record ReferrerEntry(OntologyClass cls, Slot slot) {}

    // -------------------------------------------------------------------------
    // Constraint / virtual builder
    // -------------------------------------------------------------------------

    /**
     * Attach a constraint whose primary class is this one. Delegates registration to the owning
     * spec's constraint list.
     */
    public Constraint addConstraint(String constraintName, Expr body) {
        return addConstraint(constraintName, body, Severity.ERROR, null);
    }

    /** Full-form constraint builder. */
    public Constraint addConstraint(
            String constraintName, Expr body, Severity severity, String message) {
        var spec = requireSpec("create via spec.addClass() rather than constructing directly");
        for (var c : spec.constraints()) {
            if (c.name().equals(constraintName)) {
                throw new IllegalArgumentException(
                        "Spec already has a constraint named '" + constraintName + "'");
            }
        }
        var c = new Constraint(constraintName, this, body, severity, message);
        spec.registerConstraint(c);
        return c;
    }

    /**
     * Define a virtual subclass — rows of this class that satisfy {@code where}.
     * Materialized as a SQL view at deploy time.
     */
    public VirtualClass addVirtual(String virtualName, Expr where) {
        return addVirtual(virtualName, where, null);
    }

    /** Full-form virtual builder with description. */
    public VirtualClass addVirtual(String virtualName, Expr where, String description) {
        var spec = requireSpec("create via spec.addClass() rather than constructing directly");
        if (spec.classes().containsKey(virtualName)) {
            throw new IllegalArgumentException(
                    "Spec already has a class named '" + virtualName + "'");
        }
        var vc = new VirtualClass(virtualName, this, where, description);
        spec.registerClass(virtualName, vc);
        return vc;
    }

    // -------------------------------------------------------------------------
    // Corrections binding
    // -------------------------------------------------------------------------

    /**
     * Return the {@code _user_corrections} binding for this class.
     * Throws {@link IllegalArgumentException} if corrections aren't enabled.
     */
    public SourceBinding correctionsBinding() {
        var spec = requireSpec();
        for (var b : spec.sourceBindings()) {
            if (b.source().name().equals(Spec.CORRECTIONS_SOURCE_NAME) && b.ontologyClass() == this) {
                return b;
            }
        }
        throw new IllegalArgumentException(
                "no _user_corrections binding for class '" + name
                        + "' — call spec.enableCorrections() first");
    }

    // -------------------------------------------------------------------------
    // Col accessors
    // -------------------------------------------------------------------------

    /** Returns a {@link ColAccess} bound to this class for building slot references. */
    public ColAccess col() {
        return new ColAccess(this);
    }

    /** Returns a {@link BindingsColAccess} for bindings-table system columns. */
    public BindingsColAccess bindingsCol() {
        return new BindingsColAccess(this);
    }

    // -------------------------------------------------------------------------
    // Query entry points
    // -------------------------------------------------------------------------

    private Query _query(Layer layer) {
        if (kind != ClassKind.CONCRETE) {
            throw new IllegalArgumentException(
                    "OntologyClass '" + name + "' is " + kind.value()
                            + "; only concrete classes have a " + layer.name() + " relation");
        }
        return new Query(name, null, List.of(), List.of(), null, null, null, layer, null, _spec);
    }

    /**
     * Query against {@code <class>_resolved} — the resolver's argmax view, one row per
     * canonical_id with the highest-weight non-null value per slot.
     */
    public Query resolved() {
        return _query(Layer.RESOLVED);
    }

    /**
     * Query against {@code <class>_all_sources} — per-source provenance view, one row per
     * canonical_id with each slot column as a jsonb of {@code {source_name: {value, weight}}}.
     */
    public Query allSources() {
        return _query(Layer.ALL_SOURCES);
    }

    /**
     * Query one source's claims about this class — raw bindings filtered to
     * {@code source_name = '<name>'}. The ER worker's pre-ER inspection shape.
     */
    public Query fromSource(Source source) {
        var src = source.name().replace("'", "''");
        return _query(Layer.BINDINGS).where(new Raw("source_name = '" + src + "'"));
    }

    /**
     * Bindings still waiting on entity resolution: rows in {@code <class>_bindings} with
     * {@code canonical_id IS NULL}, across every source. The ER worker's work-to-do view.
     */
    public Query unresolved() {
        return _query(Layer.BINDINGS).where(new Raw("canonical_id IS NULL"));
    }

    // -------------------------------------------------------------------------
    // Name accessors — qualified table / view names
    // -------------------------------------------------------------------------

    /**
     * {@code "<schema>.<class>_bindings"} — fully qualified name of the bindings table.
     * Reads the schema from the owning spec.
     */
    public String bindingsTableName() {
        return requireSpec().schema() + "." + name.toLowerCase() + "_bindings";
    }

    /**
     * {@code "<schema>.<class>_resolved"} — fully qualified name of the resolver's argmax view.
     */
    public String resolvedViewName() {
        return requireSpec().schema() + "." + name.toLowerCase() + "_resolved";
    }

    /**
     * {@code "<schema>.<class>_all_sources"} — fully qualified name of the per-source provenance
     * view.
     */
    public String allSourcesViewName() {
        return requireSpec().schema() + "." + name.toLowerCase() + "_all_sources";
    }

    // -------------------------------------------------------------------------
    // Explain winner
    // -------------------------------------------------------------------------

    /**
     * Return a SQL SELECT explaining who won the resolver argmax for each
     * (canonical_id, slot_name) pair. Delegates to {@link Explain}.
     *
     * @param slotName  when non-null, restrict to that one slot (typo → {@link IllegalArgumentException})
     * @param schema    postgres schema; {@code null} inherits from the owning spec
     */
    public String explainWinnerSql(String slotName, String schema) {
        return Explain.emitExplainWinnerSql(
                this,
                slotName,
                schema != null ? schema : requireSpec().schema());
    }

    /** Overload with no slot filter — explains all non-identifier slots. */
    public String explainWinnerSql() {
        return explainWinnerSql(null, null);
    }

    // -------------------------------------------------------------------------
    // Internal helpers
    // -------------------------------------------------------------------------

    Spec requireSpec() {
        if (_spec == null) {
            throw new IllegalStateException(
                    "OntologyClass '" + name + "' is not attached to a Spec "
                            + "(create via spec.addClass(...))");
        }
        return _spec;
    }

    private Spec requireSpec(String hint) {
        if (_spec == null) {
            throw new IllegalStateException(
                    "OntologyClass '" + name + "' is not attached to a Spec — " + hint);
        }
        return _spec;
    }

    @Override
    public String toString() {
        return "OntologyClass{name='" + name + "', kind=" + kind + "}";
    }
}
