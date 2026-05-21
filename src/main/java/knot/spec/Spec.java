package knot.spec;

import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import knot.ast.types.ClassRef;
import knot.ast.types.Primitive;
import knot.ast.types.TypeExpression;
import knot.compile.Constraints;
import knot.compile.Ddl;
import knot.compile.DdlOptions;

/**
 * Ontology declaration root — the spec builder entry point.
 *
 * <p>The identifier slot is spec-level: {@link #addClass(String)} auto-adds a slot named
 * {@code identifierSlotName} of {@code identifierType} to every class that doesn't already
 * inherit one. {@code identifierSlotName} is required — knot won't pick a name for you.
 *
 * <p>No spec-level {@code id} or {@code version} — see CLAUDE.md §"Smell audit".
 *
 * <p>Mirrors the Python {@code @dataclass(slots=True) Spec}. Mutable by design: classes,
 * sources, bindings, and constraints are added incrementally via the builder methods.
 */
public final class Spec {

    /** Reserved synthetic source name for human-curated overrides. */
    public static final String CORRECTIONS_SOURCE_NAME = "_user_corrections";

    private final String identifierSlotName;
    private final String schema;
    private final TypeExpression identifierType;

    /**
     * Ordered by insertion — dict semantics preserved. Keys are class/source names.
     * Access via {@link #classes()} (unmodifiable view).
     */
    private final Map<String, Object> classes;  // OntologyClass | VirtualClass
    private final Map<String, Source> sources;
    private final List<SourceBinding> sourceBindings;
    private final List<Constraint> constraints;

    /**
     * Full constructor. {@code identifierType} defaults to {@link Primitive#TEXT} when
     * {@code null} — mirrors the Python {@code __post_init__} default.
     */
    public Spec(String identifierSlotName, String schema, TypeExpression identifierType) {
        Names.checkName("Spec.identifierSlotName", identifierSlotName);
        if (schema == null || schema.isBlank()) {
            throw new IllegalArgumentException("Spec.schema must be non-blank");
        }
        this.identifierSlotName = identifierSlotName;
        this.schema = schema;
        this.identifierType = identifierType != null ? identifierType : Primitive.TEXT;
        this.classes = new LinkedHashMap<>();
        this.sources = new LinkedHashMap<>();
        this.sourceBindings = new ArrayList<>();
        this.constraints = new ArrayList<>();
    }

    /** Constructor with default {@code schema="knot_data"} and identifier type {@link Primitive#TEXT}. */
    public Spec(String identifierSlotName) {
        this(identifierSlotName, "knot_data", null);
    }

    /** Constructor with explicit schema, default identifier type. */
    public Spec(String identifierSlotName, String schema) {
        this(identifierSlotName, schema, null);
    }

    // -------------------------------------------------------------------------
    // Accessors
    // -------------------------------------------------------------------------

    public String identifierSlotName() {
        return identifierSlotName;
    }

    public String schema() {
        return schema;
    }

    public TypeExpression identifierType() {
        return identifierType;
    }

    /** Unmodifiable ordered view of all classes (OntologyClass | VirtualClass values). */
    public Map<String, Object> classes() {
        return Collections.unmodifiableMap(classes);
    }

    /** Unmodifiable ordered view of all sources. */
    public Map<String, Source> sources() {
        return Collections.unmodifiableMap(sources);
    }

    /** Unmodifiable view of source bindings in registration order. */
    public List<SourceBinding> sourceBindings() {
        return Collections.unmodifiableList(sourceBindings);
    }

    /** Unmodifiable view of constraints in registration order. */
    public List<Constraint> constraints() {
        return Collections.unmodifiableList(constraints);
    }

    // -------------------------------------------------------------------------
    // Builder methods
    // -------------------------------------------------------------------------

    /**
     * Create a concrete {@link OntologyClass}, auto-inject the identifier slot if no parent
     * contributes one, register in {@code classes}, and set the class's spec back-reference.
     * Returns the created class.
     */
    public OntologyClass addClass(String name) {
        return addClass(name, ClassKind.CONCRETE, null, null, null);
    }

    /** Full-form {@link #addClass(String)} with kind, is_a, mixins, and description. */
    public OntologyClass addClass(
            String name,
            ClassKind kind,
            OntologyClass isA,
            List<OntologyClass> mixins,
            String description) {
        if (classes.containsKey(name)) {
            throw new IllegalArgumentException("Spec already has a class named '" + name + "'");
        }
        var cls = new OntologyClass(name, kind, isA, mixins, description, this);
        classes.put(name, cls);
        // Auto-add identifier slot — skip when an ancestor already contributes one.
        boolean hasIdentifier = cls.effectiveSlots().stream().anyMatch(Slot::identifier);
        if (!hasIdentifier) {
            cls.slot(identifierSlotName, identifierType, true, true, null);
        }
        return cls;
    }

    /**
     * Create a {@link Source}, register in {@code sources}, and attach the spec back-reference.
     * Returns the created source.
     */
    public Source addSource(String name) {
        return addSource(name, null);
    }

    /** {@link #addSource(String)} with optional description. */
    public Source addSource(String name, String description) {
        if (name.equals(CORRECTIONS_SOURCE_NAME)) {
            throw new IllegalArgumentException(
                    "'" + name + "' is a reserved source name — use spec.enableCorrections() "
                            + "instead of addSource()");
        }
        if (sources.containsKey(name)) {
            throw new IllegalArgumentException("Spec already has a source named '" + name + "'");
        }
        var src = new Source(name, description, this);
        sources.put(name, src);
        return src;
    }

    /**
     * Register the {@code _user_corrections} synthetic source and bind it to every concrete
     * {@link OntologyClass} in the spec. Idempotent: calling again is a no-op if the source
     * already exists. Returns the (possibly pre-existing) {@link Source}.
     */
    public Source enableCorrections() {
        return enableCorrections("human overrides");
    }

    /** {@link #enableCorrections()} with an explicit description. */
    public Source enableCorrections(String description) {
        var existing = sources.get(CORRECTIONS_SOURCE_NAME);
        if (existing != null) {
            return existing;
        }
        var source = new Source(CORRECTIONS_SOURCE_NAME, description, this);
        sources.put(CORRECTIONS_SOURCE_NAME, source);
        for (var cls : concreteClasses()) {
            source.bind(cls);
        }
        return source;
    }

    /**
     * Merge another spec's classes, sources, source bindings, and constraints into this one.
     * Both specs must have the same {@code identifierSlotName}. Same-named classes/sources are
     * an error — the include is additive only.
     */
    public void include(Spec other) {
        if (!identifierSlotName.equals(other.identifierSlotName)) {
            throw new IllegalArgumentException(
                    "Spec.include: identifierSlotName mismatch ('"
                            + identifierSlotName + "' vs '" + other.identifierSlotName + "')");
        }
        for (var entry : other.classes.entrySet()) {
            if (classes.containsKey(entry.getKey())) {
                throw new IllegalArgumentException(
                        "Spec.include: class '" + entry.getKey() + "' already exists in target spec"
                                + " — included parts cannot redeclare classes that the parent already owns");
            }
            var cls = entry.getValue();
            // Re-root OntologyClass back-references; VirtualClass gets its ref via concreteRoot.
            if (cls instanceof OntologyClass oc) {
                oc.setSpec(this);
            }
            classes.put(entry.getKey(), cls);
        }
        for (var entry : other.sources.entrySet()) {
            if (sources.containsKey(entry.getKey())) {
                throw new IllegalArgumentException(
                        "Spec.include: source '" + entry.getKey() + "' already exists in target spec");
            }
            entry.getValue().setSpec(this);
            sources.put(entry.getKey(), entry.getValue());
        }
        sourceBindings.addAll(other.sourceBindings);
        constraints.addAll(other.constraints);
    }

    // -------------------------------------------------------------------------
    // Convenience helpers
    // -------------------------------------------------------------------------

    /**
     * Concrete {@link OntologyClass} entries — skips abstract and {@link VirtualClass}.
     * Used everywhere the emitters loop over "classes that materialize a table".
     */
    public List<OntologyClass> concreteClasses() {
        var out = new ArrayList<OntologyClass>();
        for (var v : classes.values()) {
            if (v instanceof OntologyClass oc && oc.kind() == ClassKind.CONCRETE) {
                out.add(oc);
            }
        }
        return out;
    }

    /** {@link VirtualClass} entries — backed by a view, not a table. */
    public List<VirtualClass> virtualClasses() {
        var out = new ArrayList<VirtualClass>();
        for (var v : classes.values()) {
            if (v instanceof VirtualClass vc) {
                out.add(vc);
            }
        }
        return out;
    }

    /**
     * Look up a class by name. Throws {@link IllegalArgumentException} (KeyError equivalent) if
     * not found.
     */
    public Object classByName(String name) {
        var cls = classes.get(name);
        if (cls == null) {
            throw new IllegalArgumentException("Spec has no class named '" + name + "'");
        }
        return cls;
    }

    // -------------------------------------------------------------------------
    // Package-private registration hooks — called by OntologyClass / Source / VirtualClass
    // -------------------------------------------------------------------------

    void registerClass(String name, Object cls) {
        classes.put(name, cls);
    }

    void registerBinding(SourceBinding binding) {
        sourceBindings.add(binding);
    }

    void registerConstraint(Constraint constraint) {
        constraints.add(constraint);
    }

    // -------------------------------------------------------------------------
    // Validation
    // -------------------------------------------------------------------------

    /**
     * Cross-entity well-formedness check. Raises {@link SpecError} if the spec is malformed;
     * returns normally otherwise.
     */
    public void validate() {
        var errs = _validationErrors();
        if (!errs.isEmpty()) {
            throw new SpecError("Spec failed validation:\n  - " + String.join("\n  - ", errs));
        }
    }

    /**
     * Package-private: list all cross-entity well-formedness errors. Entity-local checks
     * (name shape, enum values, body Expr typing) have already run in each entity's constructor.
     */
    List<String> _validationErrors() {
        var errs = new ArrayList<String>();

        // Build a map of OntologyClass entries only (concrete + abstract).
        var concreteOrAbstract = new LinkedHashMap<String, OntologyClass>();
        for (var v : classes.values()) {
            if (v instanceof OntologyClass oc) {
                concreteOrAbstract.put(oc.name(), oc);
            }
        }

        for (var v : classes.values()) {
            if (v instanceof OntologyClass c) {
                // is_a reference must be in spec
                if (c.isA() != null && !concreteOrAbstract.containsKey(c.isA().name())) {
                    errs.add("class '" + c.name() + "'.is_a → '" + c.isA().name() + "': not in spec");
                }
                // mixin references must be in spec
                for (var m : c.mixins()) {
                    if (!concreteOrAbstract.containsKey(m.name())) {
                        errs.add("class '" + c.name() + "' mixin '" + m.name() + "': not in spec");
                    }
                }
                // slot name uniqueness within this class's own slots
                var seenSlots = new java.util.LinkedHashSet<String>();
                for (var sl : c.slots()) {
                    if (!seenSlots.add(sl.name())) {
                        errs.add("class '" + c.name() + "' has duplicate slot '" + sl.name() + "'");
                    }
                    // ClassRef target must exist
                    if (sl.type() instanceof ClassRef cr) {
                        if (!concreteOrAbstract.containsKey(cr.target().name())) {
                            errs.add("slot " + c.name() + "." + sl.name()
                                    + " ClassRef → '" + cr.target().name() + "': not in spec");
                        }
                    }
                }
                // Concrete classes must have exactly one identifier slot (effective)
                if (c.kind() == ClassKind.CONCRETE) {
                    var ids = c.effectiveSlots().stream().filter(Slot::identifier).toList();
                    if (ids.isEmpty()) {
                        errs.add("concrete class '" + c.name() + "' has no identifier slot");
                    } else if (ids.size() > 1) {
                        var names = ids.stream().map(Slot::name).toList();
                        errs.add("concrete class '" + c.name()
                                + "' has multiple identifier slots: " + String.join(", ", names));
                    }
                }
            } else if (v instanceof VirtualClass vc) {
                // is_a may be OntologyClass or VirtualClass (nested virtual)
                var parent = vc.isA();
                if (parent instanceof OntologyClass parentCls) {
                    if (!concreteOrAbstract.containsKey(parentCls.name())) {
                        errs.add("virtual class '" + vc.name()
                                + "'.is_a → '" + parentCls.name() + "': not in spec");
                    }
                } else if (parent instanceof VirtualClass parentVc) {
                    if (!classes.containsKey(parentVc.name())) {
                        errs.add("virtual class '" + vc.name()
                                + "'.is_a → '" + parentVc.name() + "': not in spec");
                    }
                }
            }
        }

        // Virtual is_a cycle detection
        for (var v : classes.values()) {
            if (v instanceof VirtualClass vc && _virtualInCycle(vc)) {
                errs.add("virtual class '" + vc.name() + "' participates in an is_a cycle");
            }
        }

        // Constraint references
        var constraintNames = new java.util.LinkedHashSet<String>();
        for (var con : constraints) {
            if (!constraintNames.add(con.name())) {
                errs.add("duplicate constraint name '" + con.name() + "'");
            }
            if (!concreteOrAbstract.containsKey(con.primary().name())) {
                errs.add("constraint '" + con.name() + "'.primary → '"
                        + con.primary().name() + "': not in spec");
            }
        }

        // SourceBinding references
        var bindingKeys = new java.util.LinkedHashSet<String>();
        for (var b : sourceBindings) {
            var key = b.source().name() + "\0" + b.ontologyClass().name();
            if (!bindingKeys.add(key)) {
                errs.add("duplicate binding source='" + b.source().name()
                        + "' class='" + b.ontologyClass().name() + "'");
            }
            if (!sources.containsKey(b.source().name())) {
                errs.add("binding source='" + b.source().name()
                        + "' class='" + b.ontologyClass().name() + "': source not in spec");
            }
            if (!concreteOrAbstract.containsKey(b.ontologyClass().name())) {
                errs.add("binding source='" + b.source().name()
                        + "' class='" + b.ontologyClass().name() + "': class not in spec");
                continue;
            }
            var cls = concreteOrAbstract.get(b.ontologyClass().name());
            if (cls.kind() != ClassKind.CONCRETE) {
                errs.add("binding source='" + b.source().name()
                        + "' class='" + b.ontologyClass().name()
                        + "': class is " + cls.kind().value() + ", only concrete classes can bind");
                continue;
            }
            // slot_mappings keys must be effective slots on the bound class
            var effNames = cls.effectiveSlots().stream().map(Slot::name)
                    .collect(java.util.stream.Collectors.toSet());
            for (var slotName : b.slotMappings().keySet()) {
                if (!effNames.contains(slotName)) {
                    errs.add("binding source='" + b.source().name()
                            + "' class='" + b.ontologyClass().name()
                            + "': mapping references slot '" + slotName + "' not on class");
                }
            }
        }

        // is_a / mixin cycle detection
        for (var v : classes.values()) {
            if (v instanceof OntologyClass oc && _participatesInCycle(oc)) {
                errs.add("class '" + oc.name() + "' participates in an is_a / mixin cycle");
            }
        }

        return errs;
    }

    // -------------------------------------------------------------------------
    // Compile façade
    // -------------------------------------------------------------------------

    /**
     * Return the canonical CREATE script for this spec — schema, extension (when needed), tables,
     * indexes, and views. Validates first. Idempotent ({@code IF NOT EXISTS} /
     * {@code CREATE OR REPLACE}).
     */
    public String ddl() {
        return ddl(true);
    }

    /** {@link #ddl()} with control over whether views are included. */
    public String ddl(boolean includeViews) {
        validate();
        var opts = DdlOptions.builder()
                .schema(schema)
                .ifNotExists(true)
                .emitResolvedViews(includeViews)
                .emitAllSourcesViews(includeViews)
                .emitVirtualViews(includeViews)
                .build();
        return String.join("\n\n", Ddl.emitDdl(this, opts));
    }

    /**
     * Emit ONLY the {@code CREATE OR REPLACE VIEW} statements — resolved, all_sources, and
     * virtual class views. Validates first.
     */
    public String viewsDdl() {
        validate();
        var opts = DdlOptions.builder()
                .schema(schema)
                .ifNotExists(true)
                .emitBindings(false)
                .emitResolvedViews(true)
                .emitAllSourcesViews(true)
                .emitVirtualViews(true)
                .emitIndexes(false)
                .emitWeightTable(false)
                .build();
        return String.join("\n\n", Ddl.emitDdl(this, opts));
    }

    /**
     * List of {@code (constraint_name, validation_sql)} pairs. Validates first. Schema comes from
     * {@code this.schema}.
     */
    public List<ConstraintEntry> emitValidation() {
        return emitValidation(null, true);
    }

    /**
     * Full-form validation emission.
     *
     * @param scopeToSourceIdentifiers optional delta-only scope; {@code null} = full-table
     * @param includeBuiltins          when {@code true}, also emit structural invariants prefixed
     *                                 {@code _builtin_}
     */
    public List<ConstraintEntry> emitValidation(
            Map<String, List<String>> scopeToSourceIdentifiers,
            boolean includeBuiltins) {
        validate();
        return Constraints.emitValidation(this, scopeToSourceIdentifiers, includeBuiltins);
    }

    /** Immutable (name, sql) pair returned by {@link #emitValidation()}. */
    public record ConstraintEntry(String name, String sql) {}

    // -------------------------------------------------------------------------
    // Cycle-detection helpers (package-private static — unit-testable in isolation)
    // -------------------------------------------------------------------------

    /**
     * {@code true} iff {@code vc} would appear in its own virtual is_a chain. Mirrors the Python
     * module-level {@code _virtual_in_cycle}.
     */
    static boolean _virtualInCycle(VirtualClass vc) {
        var seen = new java.util.HashSet<VirtualClass>();
        var node = vc.isA();
        while (node instanceof VirtualClass parent) {
            if (parent == vc) return true;
            if (!seen.add(parent)) {
                // hit a cycle that doesn't include vc
                return false;
            }
            node = parent.isA();
        }
        return false;
    }

    /**
     * {@code true} iff {@code cls} would appear in its own is_a / mixin chain. Mirrors the Python
     * module-level {@code _participates_in_cycle}.
     */
    static boolean _participatesInCycle(OntologyClass cls) {
        var seen = new java.util.HashSet<Integer>(); // identity hash codes
        var queue = new ArrayList<OntologyClass>();
        if (cls.isA() != null) queue.add(cls.isA());
        queue.addAll(cls.mixins());
        while (!queue.isEmpty()) {
            var cur = queue.remove(0);
            if (cur == cls) return true;
            if (!seen.add(System.identityHashCode(cur))) continue;
            if (cur.isA() != null) queue.add(cur.isA());
            queue.addAll(cur.mixins());
        }
        return false;
    }

    @Override
    public String toString() {
        return "Spec{identifierSlotName='" + identifierSlotName + "', schema='" + schema + "', "
                + "classes=" + classes.keySet() + "}";
    }
}
