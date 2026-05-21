package knot.compile;

import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import knot.ast.expr.BoolOp;
import knot.ast.expr.Expr;
import knot.ast.select.Layer;
import knot.ast.types.Array;
import knot.ast.types.ClassRef;
import knot.ast.types.Enum;
import knot.ast.types.Primitive;
import knot.ast.types.TypeExpression;
import knot.ast.types.Vector;
import knot.spec.ClassKind;
import knot.spec.OntologyClass;
import knot.spec.Slot;
import knot.spec.Spec;
import knot.spec.VirtualClass;

/**
 * DDL emitter — pure function from a {@link Spec} to a list of SQL statements.
 *
 * <p>Emits one statement per artifact:
 *
 * <ul>
 *   <li>{@code CREATE SCHEMA}             — the data-plane schema</li>
 *   <li>{@code CREATE EXTENSION vector}   — only when any concrete class has a Vector slot</li>
 *   <li>{@code CREATE TABLE <class>_bindings} per concrete class (gated on
 *       {@link DdlOptions#emitBindings})</li>
 *   <li>{@code CREATE INDEX} (btree + HNSW) on bindings — gated on
 *       {@link DdlOptions#emitIndexes}</li>
 *   <li>{@code CREATE VIEW <class>_resolved} / {@code _all_sources} — delegated to
 *       {@link Resolver}</li>
 *   <li>{@code CREATE VIEW <virtual>} per {@link VirtualClass} in dependency order</li>
 *   <li>{@code COMMENT ON …} when {@link DdlOptions#emitDescriptions} is true</li>
 * </ul>
 *
 * <p>Abstract classes get no table; their slots flow into concrete subclasses via {@code is_a}
 * walks. Bindings tables carry every slot the class declares plus a
 * {@code (source_name, source_identifier)} PK pair, {@code raw_payload} for unmapped extras,
 * and {@code er_metadata} stamped at ER time. Writes are upserts on the PK — one row per
 * {@code (source, source_id)}, not SCD2 history.
 */
public final class Ddl {

    private Ddl() {}

    // ---------------------------------------------------------------------
    // Public entry point
    // ---------------------------------------------------------------------

    /** {@code emit_ddl(spec)} with Python defaults. */
    public static List<String> emitDdl(Spec spec) {
        return emitDdl(spec, DdlOptions.defaults());
    }

    /** Return the DDL statements that materialize {@code spec}. */
    public static List<String> emitDdl(Spec spec, DdlOptions options) {
        if (spec == null) {
            throw new IllegalArgumentException("spec must be non-null");
        }
        if (options == null) {
            throw new IllegalArgumentException("options must be non-null");
        }

        List<String> stmts = new ArrayList<>();
        stmts.add("CREATE SCHEMA IF NOT EXISTS " + options.schema() + ";");

        // pgvector extension — only emit when the spec actually uses it.
        // Idempotent via IF NOT EXISTS; database superuser may be required
        // the first time it runs depending on the postgres install.
        if (specHasVectorSlot(spec)) {
            stmts.add("CREATE EXTENSION IF NOT EXISTS vector;");
        }

        // Invariant weight-policy table — must exist before any resolved
        // view that LEFT JOINs against it.
        if (options.emitWeightTable()) {
            stmts.add(emitWeightTable(options.schema(), options.weightTableName(), options.ifNotExists()));
        }

        for (Object raw : spec.classes().values()) {
            if (!(raw instanceof OntologyClass cls) || cls.kind() != ClassKind.CONCRETE) {
                // Abstract OntologyClass and VirtualClass are handled below.
                continue;
            }
            // No canonical table — bindings is the only relation per class.
            // The resolved view sources canonical_ids via SELECT DISTINCT over bindings.
            if (options.emitBindings()) {
                stmts.add(emitBindingsTable(cls, options.schema(), options.bindingsSuffix(), options.ifNotExists()));
                if (options.emitIndexes()) {
                    stmts.addAll(emitBindingsIndexes(cls, options.schema(), options.bindingsSuffix(), options.ifNotExists()));
                    String tableName = cls.name().toLowerCase() + options.bindingsSuffix();
                    stmts.addAll(emitVectorIndexes(cls, tableName, options.schema(), options.ifNotExists()));
                }
            }
            if (options.emitResolvedViews() && options.emitBindings()) {
                stmts.add(Resolver.emitResolvedView(
                        spec,
                        cls,
                        options.schema(),
                        options.bindingsSuffix(),
                        options.resolvedSuffix(),
                        options.weightTableName(),
                        options.ifNotExists()));
            }
            if (options.emitAllSourcesViews() && options.emitBindings()) {
                stmts.add(Resolver.emitAllSourcesView(
                        spec,
                        cls,
                        options.schema(),
                        options.bindingsSuffix(),
                        options.allSourcesSuffix(),
                        options.weightTableName(),
                        options.ifNotExists()));
            }
        }

        // Emit virtual views in topological order (shallowest depth first) so
        // a nested virtual's CREATE VIEW can reference its parent virtual's
        // view, which must already exist.
        if (options.emitVirtualViews()) {
            List<VirtualClass> virtualClasses = new ArrayList<>();
            for (Object c : spec.classes().values()) {
                if (c instanceof VirtualClass vc) {
                    virtualClasses.add(vc);
                }
            }
            virtualClasses.sort(Comparator.comparingInt(Ddl::virtualDepth));
            for (VirtualClass vc : virtualClasses) {
                stmts.add(emitView(vc, options.schema(), options.ifNotExists()));
                if (options.emitDescriptions() && vc.description() != null && !vc.description().isEmpty()) {
                    stmts.add(commentOn("VIEW", options.schema() + "." + vc.name().toLowerCase(), vc.description()));
                }
            }
        }

        return stmts;
    }

    // ---------------------------------------------------------------------
    // Type expression → postgres type
    // ---------------------------------------------------------------------

    private static final Map<Primitive, String> PRIMITIVE_TO_PG = primitivesToPg();

    private static Map<Primitive, String> primitivesToPg() {
        Map<Primitive, String> m = new LinkedHashMap<>();
        m.put(Primitive.TEXT, "text");
        m.put(Primitive.INTEGER, "integer");
        m.put(Primitive.FLOAT, "double precision");
        m.put(Primitive.BOOLEAN, "boolean");
        m.put(Primitive.DATE, "date");
        m.put(Primitive.TIMESTAMP, "timestamptz");
        return Map.copyOf(m);
    }

    private static String pgType(TypeExpression t) {
        return switch (t) {
            case Primitive p -> {
                String s = PRIMITIVE_TO_PG.get(p);
                if (s == null) {
                    throw new IllegalArgumentException("unhandled primitive: " + p);
                }
                yield s;
            }
            case Array a -> pgType(a.of()) + "[]";
            case ClassRef ignored ->
                    // canonical_id FK, stored as text. Bindings stay loose by design —
                    // pre-ER columns hold source-ids, post-ER columns hold canonical-ids,
                    // so no postgres FK constraint is emitted.
                    "text";
            case Vector v ->
                    // pgvector type. The HNSW index is emitted separately so a spec with
                    // vector slots needs the ``vector`` extension present (gated above on
                    // {@link #specHasVectorSlot}).
                    "vector(" + v.dim() + ")";
            case Enum ignored -> "text";
        };
    }

    /**
     * Full column definition fragment: {@code <type>} for most types;
     * {@code text CHECK (<col> IS NULL OR <col> IN (...))} for Enum. The NULL branch
     * in the CHECK allows optional enum slots.
     */
    private static String pgColDef(String colName, TypeExpression t) {
        if (t instanceof Enum e) {
            var sb = new StringBuilder();
            for (int i = 0; i < e.values().size(); i++) {
                if (i > 0) {
                    sb.append(", ");
                }
                sb.append('\'').append(e.values().get(i).replace("'", "''")).append('\'');
            }
            return "text CHECK (" + colName + " IS NULL OR " + colName + " IN (" + sb + "))";
        }
        return pgType(t);
    }

    /**
     * True if any concrete class in {@code spec} declares a vector slot.
     * Used to gate {@code CREATE EXTENSION IF NOT EXISTS vector;} emission.
     */
    private static boolean specHasVectorSlot(Spec spec) {
        for (Object cls : spec.classes().values()) {
            if (!(cls instanceof OntologyClass oc) || oc.kind() != ClassKind.CONCRETE) {
                continue;
            }
            for (Slot slot : oc.effectiveSlots()) {
                if (slot.type() instanceof Vector) {
                    return true;
                }
            }
        }
        return false;
    }

    /**
     * One HNSW index per vector slot on {@code cls}, against {@code tableName}.
     * The operator class is picked by the slot's metric ({@code cosine} →
     * {@code vector_cosine_ops}, etc.).
     */
    private static List<String> emitVectorIndexes(
            OntologyClass cls, String tableName, String schema, boolean ifNotExists) {
        String maybeIfNotExists = ifNotExists ? "IF NOT EXISTS " : "";
        List<String> out = new ArrayList<>();
        for (Slot slot : cls.effectiveSlots()) {
            if (!(slot.type() instanceof Vector v)) {
                continue;
            }
            String idx = tableName + "_" + slot.name() + "_hnsw_idx";
            out.add("CREATE INDEX " + maybeIfNotExists + idx + "\n"
                    + "    ON " + schema + "." + tableName + "\n"
                    + "    USING hnsw (" + slot.name() + " " + v.hnswOps() + ");");
        }
        return out;
    }

    // ---------------------------------------------------------------------
    // Per-entity emitters
    // ---------------------------------------------------------------------

    private static String createTable(boolean ifNotExists) {
        return ifNotExists ? "CREATE TABLE IF NOT EXISTS" : "CREATE TABLE";
    }

    private static String createView(boolean ifNotExists) {
        return ifNotExists ? "CREATE OR REPLACE VIEW" : "CREATE VIEW";
    }

    /** Depth of {@code vc} in the virtual {@code is_a} chain (0 = parent is OntologyClass). */
    private static int virtualDepth(VirtualClass vc) {
        int depth = 0;
        Object node = vc.isA();
        while (node instanceof VirtualClass v) {
            depth++;
            node = v.isA();
        }
        return depth;
    }

    private static String emitView(VirtualClass vc, String schema, boolean ifNotExists) {
        // FROM always targets the concrete root's resolved view; nested
        // virtuals AND their full ancestor-predicate chain into the WHERE.
        // Chaining FROM through nested virtual views would put column refs
        // out of scope (``movie.col.year`` compiles to ``movie_resolved.year``
        // — only valid when ``movie_resolved`` is the FROM relation).
        OntologyClass concreteRoot = vc.concreteRoot();
        String fromClause = schema + "." + concreteRoot.name().toLowerCase() + "_resolved";

        // Walk the is_a chain up to (but not including) the concrete root,
        // collecting each level's predicate. AND them together — order
        // doesn't matter, AND is commutative.
        List<Expr> predicates = new ArrayList<>();
        Object cur = vc;
        while (cur instanceof VirtualClass v) {
            predicates.add(v.definition());
            cur = v.isA();
        }

        Expr combined = predicates.get(0);
        for (int i = 1; i < predicates.size(); i++) {
            combined = new BoolOp("AND", combined, predicates.get(i));
        }

        String bodySql = ExprCompiler.compileSql(combined, schema, Layer.RESOLVED, concreteRoot.name());
        return createView(ifNotExists) + " "
                + schema + "." + vc.name().toLowerCase() + " AS\n"
                + "SELECT * FROM " + fromClause + "\n"
                + "WHERE " + bodySql + ";";
    }

    private static String emitBindingsTable(
            OntologyClass cls, String schema, String bindingsSuffix, boolean ifNotExists) {
        List<String> columns = new ArrayList<>();
        columns.add("    source_name text NOT NULL");
        columns.add("    source_identifier text NOT NULL");
        for (Slot slot : cls.effectiveSlots()) {
            // All slots are nullable in bindings — including the identifier.
            // Ingest writes source rows BEFORE ER assigns a canonical_id;
            // the resolved view filters those rows out via ``WHERE <ident>
            // IS NOT NULL`` until ER claims them. A source may also only
            // project some non-identifier slots; those are nullable for the
            // same reason.
            columns.add("    " + slot.name() + " " + pgColDef(slot.name(), slot.type()));
        }
        // raw_payload — the full row as ingested, preserved for backfilling
        // new slots later without re-ingesting from the source. Always
        // populated by the write emitter; default '{}' lets legacy bindings
        // rows satisfy NOT NULL after an ALTER.
        columns.add("    raw_payload jsonb NOT NULL DEFAULT '{}'::jsonb");
        // er_metadata — caller-defined audit payload stamped at ER time
        // (run id, method, confidence, …). Empty by default; set by
        // assignCanonical / recanonicalize when callers pass er_metadata.
        // Same shape as raw_payload, different author.
        columns.add("    er_metadata jsonb NOT NULL DEFAULT '{}'::jsonb");
        // PK is (source_name, source_identifier) — one row per
        // (source, source_id) ever; re-ingest is an UPSERT, not a new
        // validity period. canonical_id is NOT part of the PK so it can
        // start NULL and be assigned later by ER.
        columns.add("    PRIMARY KEY (source_name, source_identifier)");
        String body = String.join(",\n", columns);
        return createTable(ifNotExists) + " "
                + schema + "." + cls.name().toLowerCase() + bindingsSuffix + " (\n"
                + body + "\n);";
    }

    // ---------------------------------------------------------------------
    // Comments
    // ---------------------------------------------------------------------

    /** Postgres SQL string literal escaping — double single-quotes. */
    private static String escapeComment(String text) {
        return text.replace("'", "''");
    }

    private static String commentOn(String kind, String ident, String text) {
        return "COMMENT ON " + kind + " " + ident + " IS '" + escapeComment(text) + "';";
    }

    // ---------------------------------------------------------------------
    // Bindings table indexes — resolver's per-canonical lookup
    // ---------------------------------------------------------------------

    /**
     * One btree index on canonical_id — covers the resolver's per-slot argmax
     * lookup ({@code WHERE canonical_id = X}) and the recanonicalize cascade
     * ({@code WHERE <fk_slot> = <old_canonical_id>}). Lookups by
     * {@code (source_name, source_identifier)} are already covered by the PK,
     * so no separate composite index is needed.
     */
    private static List<String> emitBindingsIndexes(
            OntologyClass cls, String schema, String bindingsSuffix, boolean ifNotExists) {
        String tableName = cls.name().toLowerCase() + bindingsSuffix;
        String table = schema + "." + tableName;
        Slot ident = cls.identifierSlot();
        String maybeIfNotExists = ifNotExists ? "IF NOT EXISTS " : "";

        List<String> out = new ArrayList<>();
        out.add("CREATE INDEX " + maybeIfNotExists + tableName + "_canonical_idx\n"
                + "    ON " + table + " (" + ident.name() + ");");
        return out;
    }

    // ---------------------------------------------------------------------
    // Weight-policy table — runtime tuning surface for source/slot weights
    // ---------------------------------------------------------------------

    /**
     * Invariant table carrying the runtime per-(source, class, slot) weight.
     * The resolver views {@code LEFT JOIN} against this table; operators tune
     * weights with plain {@code UPDATE} statements without redeploying. Weight
     * values are opaque floats — knot does not constrain or interpret them;
     * the higher value wins the argmax.
     */
    private static String emitWeightTable(String schema, String weightTableName, boolean ifNotExists) {
        String ct = ifNotExists ? "CREATE TABLE IF NOT EXISTS" : "CREATE TABLE";
        return ct + " " + schema + "." + weightTableName + " (\n"
                + "    source_name text NOT NULL,\n"
                + "    class_name  text NOT NULL,\n"
                + "    slot_name   text NOT NULL,\n"
                + "    weight      double precision NOT NULL,\n"
                + "    PRIMARY KEY (source_name, class_name, slot_name)\n"
                + ");";
    }
}
