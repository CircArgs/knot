package knot.spec;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import knot.compile.Weight;
import knot.compile.Write;

/**
 * (Source, OntologyClass) binding — what one source publishes about one class, and how its raw
 * fields map onto the class's slots.
 *
 * <p>Mutable by design: slot mappings are added incrementally via {@link #slot(String, String,
 * String)}. The back-reference to {@link Spec} is reached via the owning {@link Source}.
 *
 * <p>Mirrors the Python {@code @dataclass(slots=True) SourceBinding}.
 */
public final class SourceBinding {

    private final Source source;
    private final OntologyClass class_;
    private final Map<String, SlotMapping> slotMappings;
    private final String description;

    /**
     * Constructor used by {@link Source#bind(OntologyClass)} and {@link Source#bind(OntologyClass,
     * String)}.
     */
    SourceBinding(Source source, OntologyClass class_, String description) {
        if (source == null) {
            throw new IllegalArgumentException("SourceBinding.source must be non-null");
        }
        if (class_ == null) {
            throw new IllegalArgumentException("SourceBinding.class_ must be non-null");
        }
        this.source = source;
        this.class_ = class_;
        this.slotMappings = new LinkedHashMap<>();
        this.description = description;
    }

    /** Bare constructor without description. */
    SourceBinding(Source source, OntologyClass class_) {
        this(source, class_, null);
    }

    // -------------------------------------------------------------------------
    // Accessors
    // -------------------------------------------------------------------------

    public Source source() {
        return source;
    }

    /** The bound {@link OntologyClass}. Named {@code ontologyClass()} in Java to avoid the
     * trailing underscore; internally still {@code class_}. */
    public OntologyClass ontologyClass() {
        return class_;
    }

    public Map<String, SlotMapping> slotMappings() {
        return slotMappings;
    }

    public String description() {
        return description;
    }

    /** The class's identifier slot — same as {@code class_.identifierSlot()}. */
    public Slot identifierSlot() {
        return class_.identifierSlot();
    }

    /**
     * {@code "<schema>.<class>_bindings"} — the table this binding writes to.
     * Same as {@code class_.bindingsTableName()}.
     */
    public String bindingsTableName() {
        return class_.bindingsTableName();
    }

    // -------------------------------------------------------------------------
    // Slot mapping declaration
    // -------------------------------------------------------------------------

    /**
     * Declare an explicit mapping for one class slot.
     *
     * <ul>
     *   <li>{@code classSlot} is required.
     *   <li>{@code sourceSlot} defaults to {@code classSlot} when {@code null}.
     *   <li>{@code sql} is the optional postgres expression over those fields; {@code null} means
     *       passthrough of {@code sourceSlot}.
     * </ul>
     *
     * <p>Returns {@code this} for chaining.
     */
    public SourceBinding slot(String classSlot, String sourceSlot, String sql) {
        // Validates the class slot exists — IAE on typo.
        class_.getSlot(classSlot);
        var effective = sourceSlot != null ? sourceSlot : classSlot;
        slotMappings.put(classSlot, new SlotMapping(classSlot, List.of(effective), sql));
        return this;
    }

    /** Passthrough mapping — no SQL transform. */
    public SourceBinding slot(String classSlot, String sourceSlot) {
        return slot(classSlot, sourceSlot, null);
    }

    /** Same-name passthrough mapping — {@code sourceSlot = classSlot}, no SQL transform. */
    public SourceBinding slot(String classSlot) {
        return slot(classSlot, classSlot, null);
    }

    /**
     * Return the effective {@link SlotMapping} for a class slot — the explicit one if declared,
     * otherwise an implicit passthrough (same name, no SQL transform).
     */
    public SlotMapping effectiveMapping(String slotName) {
        var explicit = slotMappings.get(slotName);
        if (explicit != null) return explicit;
        return SlotMapping.of(slotName, slotName);
    }

    // -------------------------------------------------------------------------
    // Write SQL facades — delegate to knot.compile.Write
    // -------------------------------------------------------------------------

    /**
     * Return one upsert SQL template for this binding. References a single {@code %(rows)s::jsonb}
     * parameter — the host's connector binds the rows.
     */
    public String writeSql() {
        return writeSql(null);
    }

    /**
     * Upsert SQL with optional RETURNING clause. {@code returning=null} omits the clause;
     * {@code "*"} returns all columns; a comma-separated list returns named columns.
     */
    public String writeSql(String returning) {
        _requireSpec();
        return Write.emitBindingWriteSql(this, returning);
    }

    /**
     * SELECT SQL template that validates rows BEFORE upsert. Takes the same {@code %(rows)s::jsonb}
     * parameter as {@link #writeSql()} and returns a result set of violations.
     */
    public String validateRowsSql() {
        _requireSpec();
        return Write.emitValidateRowsSql(this);
    }

    /**
     * SQL template that updates ONE slot value across a batch of existing binding rows. One named
     * placeholder — {@code %(rows)s::jsonb}.
     */
    public String updateSlotSql(String slotName) {
        _requireSpec();
        return Write.emitUpdateSlotSql(this, slotName);
    }

    /**
     * SQL template that retracts (deletes) one binding row. Two named placeholders —
     * {@code %(canonical_id)s}, {@code %(source_identifier)s}.
     */
    public String retractSql() {
        _requireSpec();
        return Write.emitRetractSql(this);
    }

    /**
     * SQL template that assigns a {@code canonical_id} to one unresolved binding row. Three named
     * placeholders — {@code %(canonical_id)s}, {@code %(source_identifier)s},
     * {@code %(er_metadata)s}.
     */
    public String assignCanonicalSql() {
        _requireSpec();
        return Write.emitAssignCanonicalSql(this);
    }

    /**
     * SQL template that assigns {@code canonical_id} to a batch of unresolved binding rows in one
     * round-trip. Single {@code %(assignments)s::jsonb} placeholder.
     */
    public String assignCanonicalsSql() {
        _requireSpec();
        return Write.emitAssignCanonicalsSql(this);
    }

    /**
     * SQL template that reassigns a binding row's {@code canonical_id}. Three named placeholders —
     * {@code %(new_canonical_id)s}, {@code %(source_identifier)s}, {@code %(er_metadata)s}.
     */
    public String recanonicalizeSql() {
        _requireSpec();
        return Write.emitRecanonicalizeSql(this);
    }

    // -------------------------------------------------------------------------
    // Weight SQL facades — delegate to knot.compile.Weight
    // -------------------------------------------------------------------------

    /** INSERT … ON CONFLICT UPDATE for one slot weight. Binds {@code %(slot_name)s}, {@code %(weight)s}. */
    public String upsertWeightSql() {
        _requireSpec();
        return Weight.emitUpsertWeightSql(this);
    }

    /** Bulk-set N weights in one statement. Binds {@code %(weights)s::jsonb} ({@code {slot_name: weight, …}}). */
    public String upsertWeightsSql() {
        _requireSpec();
        return Weight.emitUpsertWeightsSql(this);
    }

    /** SELECT this binding's currently-stored weights — rows of {@code (slot_name, weight)}. */
    public String readWeightsSql() {
        _requireSpec();
        return Weight.emitReadWeightsSql(this);
    }

    /**
     * DELETE the {@code (source, class, slot)} weight row. Reverts to the resolver's
     * {@code COALESCE(weight, 0)} fallback. {@link IllegalArgumentException} on unknown
     * {@code slotName}.
     */
    public String deleteWeightSql(String slotName) {
        _requireSpec();
        return Weight.emitDeleteWeightSql(this, slotName);
    }

    // -------------------------------------------------------------------------
    // Internal helpers
    // -------------------------------------------------------------------------

    /**
     * Resolve the owning {@link Spec} via the source's back-reference. Package-private — the
     * compile modules use this to get the schema name.
     */
    Spec _requireSpec() {
        var spec = source.spec();
        if (spec == null) {
            throw new IllegalStateException(
                    "binding '" + source.name() + "' → '" + class_.name()
                            + "' is not attached to a Spec");
        }
        return spec;
    }

    @Override
    public String toString() {
        return "SourceBinding{source='" + source.name() + "', class='" + class_.name() + "'}";
    }
}
