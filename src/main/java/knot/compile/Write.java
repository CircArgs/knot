package knot.compile;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;

import knot.ast.types.Array;
import knot.ast.types.ClassRef;
import knot.ast.types.Enum;
import knot.ast.types.Primitive;
import knot.ast.types.TypeExpression;
import knot.ast.types.Vector;
import knot.spec.ClassKind;
import knot.spec.OntologyClass;
import knot.spec.Slot;
import knot.spec.SourceBinding;

/**
 * Write-data path — upsert + ER stamp SQL templates.
 *
 * <p>Every public static method returns a SQL string (or throws on invalid input). No I/O,
 * no connection ownership. Mirrors {@code knot/compile/write.py}.
 *
 * <p>SQL templates use postgres named-placeholder syntax — {@code %(name)s} — so the host
 * passes a parameter map to its JDBC/psycopg connector.
 */
public final class Write {

    private Write() {}

    // -------------------------------------------------------------------------
    // Public entry points
    // -------------------------------------------------------------------------

    /**
     * Return one upsert SQL template for the binding. References a single
     * {@code %(rows)s::jsonb} parameter. Re-ingesting the same source/source_id
     * upserts in place; {@code canonical_id} and {@code er_metadata} (ER-owned) are
     * preserved across re-ingests; every other slot + {@code raw_payload} gets
     * overwritten.
     *
     * <p>Uses schema + suffix from the binding's owning spec (via
     * {@code _requireSpec().schema()}).
     *
     * @param returning {@code null} — no RETURNING clause; {@code "*"} — {@code RETURNING *};
     *                  any other non-null string — treated as a slot name list
     *                  (comma-separated or single). Pass {@code null} for the common case.
     */
    public static String emitBindingWriteSql(SourceBinding b, String returning) {
        var spec = b._requireSpec();
        return emitBindingWriteSql(b, returning,
                new WriteOptions(spec.schema(), "_bindings"));
    }

    /**
     * Full-form overload with explicit {@link WriteOptions}. The facade on
     * {@link SourceBinding} delegates to the no-options overload; this overload
     * is for tests that pass {@code schema} / {@code bindingsSuffix} explicitly.
     *
     * @param returning  {@code null} — no RETURNING clause; {@code "*"} — RETURNING *;
     *                   {@code List<String>} of slot names — validated then emitted.
     *                   Accepts either a plain {@code String} or a {@code List<String>}
     *                   via the {@code Object returning} overload.
     */
    public static String emitBindingWriteSql(SourceBinding b, Object returning, WriteOptions opts) {
        checkConcrete(b.ontologyClass());
        String sql = emitClassUpsert(b, opts, "rows");

        if (returning == null) {
            return sql;
        }
        String returningClause;
        if ("*".equals(returning)) {
            returningClause = "RETURNING *";
        } else if (returning instanceof List<?> list) {
            var effNames = new LinkedHashSet<String>();
            for (var s : b.ontologyClass().effectiveSlots()) {
                effNames.add(s.name());
            }
            var cols = new ArrayList<String>();
            for (var item : list) {
                String name = item.toString();
                if (!effNames.contains(name)) {
                    throw new IllegalArgumentException(
                            "returning: slot '" + name + "' not on class '"
                                    + b.ontologyClass().name() + "'; valid slots: "
                                    + new ArrayList<>(effNames).stream().sorted().toList());
                }
                cols.add(name);
            }
            returningClause = "RETURNING " + String.join(", ", cols);
        } else {
            // Single string slot name
            String name = returning.toString();
            var effNames = new LinkedHashSet<String>();
            for (var s : b.ontologyClass().effectiveSlots()) {
                effNames.add(s.name());
            }
            if (!effNames.contains(name)) {
                throw new IllegalArgumentException(
                        "returning: slot '" + name + "' not on class '"
                                + b.ontologyClass().name() + "'; valid slots: "
                                + new ArrayList<>(effNames).stream().sorted().toList());
            }
            returningClause = "RETURNING " + name;
        }
        // Strip trailing semicolon, append RETURNING, re-add semicolon.
        String base = sql.endsWith(";") ? sql.substring(0, sql.length() - 1) : sql;
        return base + "\n" + returningClause + ";";
    }

    /**
     * SELECT SQL template that validates rows BEFORE upsert. Takes the same
     * {@code %(rows)s::jsonb} parameter and returns a result set of violations — zero
     * rows means every input row is well-formed.
     *
     * <p>Output columns: {@code row_index}, {@code source_identifier},
     * {@code violation_kind}, {@code slot_name}, {@code detail}, {@code payload}.
     */
    public static String emitValidateRowsSql(SourceBinding b) {
        var spec = b._requireSpec();
        return emitValidateRowsSql(b, new WriteOptions(spec.schema(), "_bindings"));
    }

    /** Full-form overload accepting explicit {@link WriteOptions}. */
    public static String emitValidateRowsSql(SourceBinding b, WriteOptions opts) {
        checkConcrete(b.ontologyClass());
        var cls = b.ontologyClass();
        var parts = new ArrayList<String>();

        // CTE: expand rows with 0-based index.
        String inputCte =
                "WITH input AS (\n"
                        + "  SELECT (row_number() OVER ()) - 1 AS row_index,\n"
                        + "         r AS payload\n"
                        + "  FROM jsonb_array_elements(%(rows)s::jsonb) AS r\n"
                        + ")";

        // Check 1: missing source_identifier
        parts.add(
                "SELECT\n"
                        + "  row_index,\n"
                        + "  NULL::text AS source_identifier,\n"
                        + "  'missing_source_identifier'::text AS violation_kind,\n"
                        + "  NULL::text AS slot_name,\n"
                        + "  'source_identifier is required'::text AS detail,\n"
                        + "  payload\n"
                        + "FROM input\n"
                        + "WHERE payload->>'source_identifier' IS NULL");

        // Check 2: missing required slots (non-identifier)
        for (var slot : cls.effectiveSlots()) {
            if (slot.identifier()) continue;
            if (!slot.required()) continue;
            var m = b.effectiveMapping(slot.name());
            String srcField = m.sourceSlot().get(0);
            String slotLit = sqlLiteral(slot.name());
            String srcLit = sqlLiteral(srcField);
            parts.add(
                    "SELECT\n"
                            + "  row_index,\n"
                            + "  payload->>'source_identifier',\n"
                            + "  'missing_required_slot'::text AS violation_kind,\n"
                            + "  " + slotLit + "::text AS slot_name,\n"
                            + "  'required slot was absent or null'::text AS detail,\n"
                            + "  payload\n"
                            + "FROM input\n"
                            + "WHERE NOT (payload ? " + srcLit + ")\n"
                            + "   OR payload->>" + srcLit + " IS NULL");
        }

        // Check 3: type coercion pre-checks per slot type
        for (var slot : cls.effectiveSlots()) {
            if (slot.identifier()) continue;
            var m = b.effectiveMapping(slot.name());
            // Skip slots with an explicit SQL expression — user owns the transform.
            if (m.sql() != null) continue;
            String srcField = m.sourceSlot().get(0);
            String slotLit = sqlLiteral(slot.name());
            String srcLit = sqlLiteral(srcField);

            String check = typePrecheck(slot.type(), srcField, srcLit);
            if (check == null) continue;

            String detail = typePrecheckDetail(slot.type());
            String detailLit = sqlLiteral(detail);
            parts.add(
                    "SELECT\n"
                            + "  row_index,\n"
                            + "  payload->>'source_identifier',\n"
                            + "  'type_coercion_failed'::text AS violation_kind,\n"
                            + "  " + slotLit + "::text AS slot_name,\n"
                            + "  " + detailLit + "::text AS detail,\n"
                            + "  payload\n"
                            + "FROM input\n"
                            + "WHERE payload ? " + srcLit + "\n"
                            + "  AND payload->>" + srcLit + " IS NOT NULL\n"
                            + "  AND " + check);
        }

        String unionBody = String.join("\nUNION ALL\n", parts);
        return inputCte + "\n" + unionBody + ";";
    }

    /**
     * SQL template that updates ONE slot value on existing binding rows. Batched via
     * {@code %(rows)s::jsonb} — same shape as the upsert but a slot-level UPDATE.
     *
     * @param slotName class slot name to update
     * @throws IllegalArgumentException if {@code slotName} is the identifier slot or unknown
     */
    public static String emitUpdateSlotSql(SourceBinding b, String slotName) {
        var spec = b._requireSpec();
        return emitUpdateSlotSql(b, slotName, new WriteOptions(spec.schema(), "_bindings"));
    }

    /** Full-form overload accepting explicit {@link WriteOptions}. */
    public static String emitUpdateSlotSql(SourceBinding b, String slotName, WriteOptions opts) {
        checkConcrete(b.ontologyClass());
        var cls = b.ontologyClass();
        // KeyError on typo — getSlot() throws IAE which matches Python KeyError convention.
        var slot = cls.getSlot(slotName);
        if (slot.identifier()) {
            throw new IllegalArgumentException(
                    "emitUpdateSlotSql can't target the identifier slot ('"
                            + slotName + "'); use emitAssignCanonicalSql / emitRecanonicalizeSql "
                            + "for canonical_id changes");
        }
        String table = bindingsTableId(cls, opts.schema(), opts.bindingsSuffix());
        String sourceLiteral = sqlLiteral(b.source().name());
        String valueExpr = jsonbExtract(slot, null);
        return "UPDATE " + table + " AS b\n"
                + "SET " + slot.name() + " = " + valueExpr + "\n"
                + "FROM jsonb_array_elements(%(rows)s::jsonb) AS r\n"
                + "WHERE b.source_name = " + sourceLiteral + "\n"
                + "  AND b.source_identifier = (r->>'source_identifier');";
    }

    /**
     * DELETE SQL template that retracts (deletes) one binding row. Two named placeholders —
     * {@code %(canonical_id)s} and {@code %(source_identifier)s}.
     */
    public static String emitRetractSql(SourceBinding b) {
        var spec = b._requireSpec();
        return emitRetractSql(b, new WriteOptions(spec.schema(), "_bindings"));
    }

    /** Full-form overload accepting explicit {@link WriteOptions}. */
    public static String emitRetractSql(SourceBinding b, WriteOptions opts) {
        checkConcrete(b.ontologyClass());
        var ident = b.ontologyClass().identifierSlot();
        String table = bindingsTableId(b.ontologyClass(), opts.schema(), opts.bindingsSuffix());
        String sourceLiteral = sqlLiteral(b.source().name());
        return "DELETE FROM " + table + "\n"
                + "WHERE " + ident.name() + " = %(canonical_id)s\n"
                + "  AND source_name = " + sourceLiteral + "\n"
                + "  AND source_identifier = %(source_identifier)s;";
    }

    /**
     * CTE-chain SQL template that assigns a {@code canonical_id} to one unresolved binding
     * row. Three named placeholders — {@code %(canonical_id)s}, {@code %(source_identifier)s},
     * {@code %(er_metadata)s}.
     *
     * <p>Atomically: (1) stamp the binding; (2) forward-translate ClassRef FK columns
     * (COALESCE keeps source-id if target not yet ER'd); (3) backward fan-out to every
     * referencing class (grouped per class — one CTE per referencing CLASS, CASE WHEN
     * per FK slot, to avoid multiple-modifying-CTE undefined behavior in postgres).
     */
    public static String emitAssignCanonicalSql(SourceBinding b) {
        var spec = b._requireSpec();
        return emitAssignCanonicalSql(b, new WriteOptions(spec.schema(), "_bindings"));
    }

    /** Full-form overload accepting explicit {@link WriteOptions}. */
    public static String emitAssignCanonicalSql(SourceBinding b, WriteOptions opts) {
        checkConcrete(b.ontologyClass());
        var cls = b.ontologyClass();
        String identName = cls.identifierSlot().name();
        String table = bindingsTableId(cls, opts.schema(), opts.bindingsSuffix());
        String sourceLiteral = sqlLiteral(b.source().name());

        // Forward FK translation — for each ClassRef slot on this class.
        var setClauses = new ArrayList<String>();
        setClauses.add(identName + " = %(canonical_id)s");
        setClauses.add("er_metadata = COALESCE(%(er_metadata)s::jsonb, er_metadata)");
        for (var slot : cls.effectiveSlots()) {
            if (!(slot.type() instanceof ClassRef cr)) continue;
            var target = cr.target();
            String targetTable = bindingsTableId(target, opts.schema(), opts.bindingsSuffix());
            String targetIdent = target.identifierSlot().name();
            String lookup = "(SELECT " + targetIdent + " FROM " + targetTable
                    + " WHERE source_name = " + sourceLiteral
                    + " AND source_identifier = " + table + "." + slot.name()
                    + " AND " + targetIdent + " IS NOT NULL LIMIT 1)";
            setClauses.add(slot.name() + " = COALESCE(" + lookup + ", " + table + "." + slot.name() + ")");
        }
        String setBlock = String.join(",\n    ", setClauses);

        // Backward fan-out — group referrers by class.
        // OntologyClass is mutable (not hashable by identity in a Map), so key by name.
        var byRefCls = new LinkedHashMap<String, RefClsEntry>();
        for (var entry : cls.referrers()) {
            byRefCls.computeIfAbsent(entry.cls().name(), k -> new RefClsEntry(entry.cls()))
                    .slots.add(entry.slot());
        }

        var fanoutCtes = new ArrayList<String>();
        for (var e : byRefCls.values()) {
            String refTable = bindingsTableId(e.cls, opts.schema(), opts.bindingsSuffix());
            String cteName = "fanout_" + e.cls.name().toLowerCase();
            var sets = new ArrayList<String>();
            var wheres = new ArrayList<String>();
            for (var refSlot : e.slots) {
                sets.add(refSlot.name() + " = CASE WHEN " + refSlot.name()
                        + " = %(source_identifier)s THEN %(canonical_id)s"
                        + " ELSE " + refSlot.name() + " END");
                wheres.add(refSlot.name() + " = %(source_identifier)s");
            }
            String setClause = String.join(",\n      ", sets);
            String whereClause = String.join(" OR ", wheres);
            fanoutCtes.add(cteName + " AS (\n"
                    + "  UPDATE " + refTable + "\n"
                    + "  SET " + setClause + "\n"
                    + "  WHERE EXISTS (SELECT 1 FROM stamp)\n"
                    + "    AND source_name = " + sourceLiteral + "\n"
                    + "    AND (" + whereClause + ")\n"
                    + ")");
        }

        var allCtes = new ArrayList<String>();
        allCtes.add("stamp AS (\n"
                + "  UPDATE " + table + "\n"
                + "  SET " + setBlock + "\n"
                + "  WHERE source_name = " + sourceLiteral + "\n"
                + "    AND source_identifier = %(source_identifier)s\n"
                + "    AND " + identName + " IS NULL\n"
                + "  RETURNING " + identName + "\n"
                + ")");
        allCtes.addAll(fanoutCtes);

        return "WITH " + String.join(",\n", allCtes) + "\nSELECT 1;";
    }

    /**
     * Batched version of {@link #emitAssignCanonicalSql}. Driven by
     * {@code jsonb_to_recordset(%(assignments)s::jsonb) AS a(canonical_id text,
     * source_identifier text, er_metadata jsonb)}. Fan-out CTEs use scalar subqueries
     * against the stamp CTE. Idempotency semantics identical to the singular form.
     */
    public static String emitAssignCanonicalsSql(SourceBinding b) {
        var spec = b._requireSpec();
        return emitAssignCanonicalsSql(b, new WriteOptions(spec.schema(), "_bindings"));
    }

    /** Full-form overload accepting explicit {@link WriteOptions}. */
    public static String emitAssignCanonicalsSql(SourceBinding b, WriteOptions opts) {
        checkConcrete(b.ontologyClass());
        var cls = b.ontologyClass();
        String identName = cls.identifierSlot().name();
        String table = bindingsTableId(cls, opts.schema(), opts.bindingsSuffix());
        String sourceLiteral = sqlLiteral(b.source().name());

        // Forward FK translation — source_identifier comes from the recordset alias 'a'.
        var setClauses = new ArrayList<String>();
        setClauses.add(identName + " = a.canonical_id");
        setClauses.add("er_metadata = COALESCE(a.er_metadata, b.er_metadata)");
        for (var slot : cls.effectiveSlots()) {
            if (!(slot.type() instanceof ClassRef cr)) continue;
            var target = cr.target();
            String targetTable = bindingsTableId(target, opts.schema(), opts.bindingsSuffix());
            String targetIdent = target.identifierSlot().name();
            String lookup = "(SELECT " + targetIdent + " FROM " + targetTable
                    + " WHERE source_name = " + sourceLiteral
                    + " AND source_identifier = b." + slot.name()
                    + " AND " + targetIdent + " IS NOT NULL LIMIT 1)";
            setClauses.add(slot.name() + " = COALESCE(" + lookup + ", b." + slot.name() + ")");
        }
        String setBlock = String.join(",\n    ", setClauses);

        // Backward fan-out — group by referring class (same UB-avoidance logic).
        var byRefCls = new LinkedHashMap<String, RefClsEntry>();
        for (var entry : cls.referrers()) {
            byRefCls.computeIfAbsent(entry.cls().name(), k -> new RefClsEntry(entry.cls()))
                    .slots.add(entry.slot());
        }

        var fanoutCtes = new ArrayList<String>();
        for (var e : byRefCls.values()) {
            String refTable = bindingsTableId(e.cls, opts.schema(), opts.bindingsSuffix());
            String cteName = "fanout_" + e.cls.name().toLowerCase();
            var sets = new ArrayList<String>();
            var wheres = new ArrayList<String>();
            for (var refSlot : e.slots) {
                // Scalar subquery against stamp CTE; rewrite iff held by stamp.
                sets.add(refSlot.name() + " = COALESCE("
                        + "(SELECT canonical_id FROM stamp WHERE source_identifier = r." + refSlot.name() + "), "
                        + "r." + refSlot.name() + ")");
                wheres.add("EXISTS (SELECT 1 FROM stamp WHERE source_identifier = r." + refSlot.name() + ")");
            }
            String setClause = String.join(",\n      ", sets);
            String whereClause = String.join(" OR ", wheres);
            fanoutCtes.add(cteName + " AS (\n"
                    + "  UPDATE " + refTable + " AS r\n"
                    + "  SET " + setClause + "\n"
                    + "  WHERE r.source_name = " + sourceLiteral + "\n"
                    + "    AND (" + whereClause + ")\n"
                    + ")");
        }

        var allCtes = new ArrayList<String>();
        allCtes.add("stamp AS (\n"
                + "  UPDATE " + table + " AS b\n"
                + "  SET " + setBlock + "\n"
                + "  FROM jsonb_to_recordset(%(assignments)s::jsonb)\n"
                + "       AS a(canonical_id text, source_identifier text, er_metadata jsonb)\n"
                + "  WHERE b.source_name = " + sourceLiteral + "\n"
                + "    AND b.source_identifier = a.source_identifier\n"
                + "    AND b." + identName + " IS NULL\n"
                + "  RETURNING b." + identName + " AS canonical_id, b.source_identifier\n"
                + ")");
        allCtes.addAll(fanoutCtes);

        return "WITH " + String.join(",\n", allCtes) + "\nSELECT 1;";
    }

    /**
     * CTE-chain SQL template that reassigns a binding row's {@code canonical_id}. Three named
     * placeholders — {@code %(new_canonical_id)s}, {@code %(source_identifier)s},
     * {@code %(er_metadata)s}. Cascade rewrites every referencing class's FK column
     * (source-agnostic — canonical-ids are global).
     */
    public static String emitRecanonicalizeSql(SourceBinding b) {
        var spec = b._requireSpec();
        return emitRecanonicalizeSql(b, new WriteOptions(spec.schema(), "_bindings"));
    }

    /** Full-form overload accepting explicit {@link WriteOptions}. */
    public static String emitRecanonicalizeSql(SourceBinding b, WriteOptions opts) {
        checkConcrete(b.ontologyClass());
        String identName = b.ontologyClass().identifierSlot().name();
        String table = bindingsTableId(b.ontologyClass(), opts.schema(), opts.bindingsSuffix());
        String sourceLiteral = sqlLiteral(b.source().name());

        // Cascade — every (ref_class, fk_slot) pointing at this class.
        // One CTE per (ref_class, fk_slot) pair (recanonicalize is source-agnostic,
        // so no class grouping needed — FK columns are already canonical-ids post-ER).
        var cascadeCtes = new ArrayList<String>();
        for (var entry : b.ontologyClass().referrers()) {
            String refTable = bindingsTableId(entry.cls(), opts.schema(), opts.bindingsSuffix());
            String cteName = "cascade_" + entry.cls().name().toLowerCase() + "_" + entry.slot().name();
            cascadeCtes.add(cteName + " AS (\n"
                    + "  UPDATE " + refTable + "\n"
                    + "  SET " + entry.slot().name() + " = %(new_canonical_id)s\n"
                    + "  WHERE " + entry.slot().name() + " = (SELECT old_id FROM old_state)\n"
                    + ")");
        }

        var allCtes = new ArrayList<String>();
        // Capture OLD canonical_id BEFORE the stamp UPDATE rewrites it.
        allCtes.add("old_state AS (\n"
                + "  SELECT " + identName + " AS old_id\n"
                + "  FROM " + table + "\n"
                + "  WHERE source_name = " + sourceLiteral + "\n"
                + "    AND source_identifier = %(source_identifier)s\n"
                + "    AND " + identName + " IS NOT NULL\n"
                + ")");
        allCtes.add("stamp AS (\n"
                + "  UPDATE " + table + "\n"
                + "  SET " + identName + " = %(new_canonical_id)s,\n"
                + "      er_metadata = COALESCE(%(er_metadata)s::jsonb, er_metadata)\n"
                + "  WHERE source_name = " + sourceLiteral + "\n"
                + "    AND source_identifier = %(source_identifier)s\n"
                + "    AND " + identName + " IS NOT NULL\n"
                + ")");
        allCtes.addAll(cascadeCtes);

        return "WITH " + String.join(",\n", allCtes) + "\nSELECT 1;";
    }

    // -------------------------------------------------------------------------
    // Private helpers
    // -------------------------------------------------------------------------

    /** Check that the class is CONCRETE; throws {@link IllegalArgumentException} otherwise. */
    private static void checkConcrete(OntologyClass cls) {
        if (cls.kind() != ClassKind.CONCRETE) {
            throw new IllegalArgumentException(
                    "class '" + cls.name() + "' is '" + cls.kind().value()
                            + "'; only concrete classes have bindings tables");
        }
    }

    /** Fully-qualified bindings table name: {@code schema.classname<suffix>}. */
    private static String bindingsTableId(OntologyClass cls, String schema, String suffix) {
        return schema + "." + cls.name().toLowerCase() + suffix;
    }

    /** Single-quote a SQL string literal, escaping interior single-quotes by doubling. */
    static String sqlLiteral(String s) {
        return "'" + escapeLit(s) + "'";
    }

    /** Escape interior single-quotes by doubling. */
    private static String escapeLit(String s) {
        return s.replace("'", "''");
    }

    /**
     * Postgres cast suffix for pulling a typed value out of a jsonb element.
     * Returns {@code "::text"} for ClassRef and Enum (both stored as text).
     * Returns {@code "::<type>[]"} for Array with the inner type resolved recursively.
     */
    private static String pgCast(TypeExpression t) {
        return switch (t) {
            case Primitive p -> switch (p) {
                case TEXT      -> "::text";
                case INTEGER   -> "::integer";
                case FLOAT     -> "::double precision";
                case BOOLEAN   -> "::boolean";
                case DATE      -> "::date";
                case TIMESTAMP -> "::timestamptz";
            };
            case Array a -> {
                // Strip leading "::" from the inner cast to build the array cast.
                String inner = pgCast(a.of()).substring(2); // remove leading "::"
                yield "::" + inner + "[]";
            }
            case ClassRef ignored -> "::text";
            case Enum ignored    -> "::text";
            case Vector v        -> "::vector(" + v.dim() + ")";
        };
    }

    /**
     * SQL fragment that pulls {@code rawField} (or {@code slot.name()} when null) out of
     * the {@code r} jsonb element with the correct cast.
     */
    private static String jsonbExtract(Slot slot, String rawField) {
        String field = rawField != null ? rawField : slot.name();
        if (slot.type() instanceof Array a) {
            // jsonb arrays must round-trip through unnest+ARRAY.
            String inner = pgCast(a.of()).substring(2); // strip "::"
            return "(SELECT ARRAY(SELECT (value #>> '{}')::" + inner
                    + " FROM jsonb_array_elements(r->'" + field + "') AS value))";
        }
        String cast = pgCast(slot.type());
        return "(r->>'" + field + "')" + cast;
    }

    /**
     * Value expression for a passthrough mapping using the {@code raw} subquery's text alias.
     */
    private static String passthroughValue(Slot slot, String rawField) {
        return switch (slot.type()) {
            case Primitive p -> "raw." + rawField + pgCast(p);
            case ClassRef ignored -> "raw." + rawField + "::text";
            case Enum ignored    -> "raw." + rawField + "::text";
            case Vector v        -> "raw." + rawField + "::vector(" + v.dim() + ")";
            case Array a -> {
                // Array passthrough needs the original jsonb element (text→text[] doesn't cast).
                // Use the preserved __raw_payload.
                String inner = pgCast(a.of()).substring(2); // strip "::"
                yield "(SELECT ARRAY(SELECT (value #>> '{}')::" + inner
                        + " FROM jsonb_array_elements(raw.__raw_payload->'" + rawField + "') AS value))";
            }
        };
    }

    /**
     * WHERE fragment (truthy = violation) for structural type pre-checks.
     * Returns {@code null} when no cheap check is available for this type
     * (TEXT, ClassRef, Array).
     */
    private static String typePrecheck(TypeExpression t, String srcField, String srcLit) {
        return switch (t) {
            case Primitive p -> switch (p) {
                case INTEGER   -> "payload->>" + srcLit + " !~ '^-?[0-9]+$'";
                case FLOAT     -> "payload->>" + srcLit
                        + " !~ '^-?[0-9]+(\\.[0-9]+)?([eE][+-]?[0-9]+)?$'";
                case BOOLEAN   -> "lower(payload->>" + srcLit + ")"
                        + " NOT IN ('true', 'false', '1', '0', 'yes', 'no', 't', 'f')";
                case DATE      -> "payload->>" + srcLit
                        + " !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'";
                case TIMESTAMP -> "payload->>" + srcLit
                        + " !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}[T ][0-9]{2}:[0-9]{2}'";
                case TEXT      -> null;
            };
            case Vector v -> "NOT (jsonb_typeof(payload->'" + srcField + "') = 'array'\n"
                    + "     AND jsonb_array_length(payload->'" + srcField + "') = " + v.dim() + ")";
            case Enum e -> {
                var vals = new ArrayList<String>();
                for (var v : e.values()) {
                    vals.add("'" + escapeLit(v) + "'");
                }
                yield "payload->>" + srcLit + " NOT IN (" + String.join(", ", vals) + ")";
            }
            case ClassRef ignored -> null;
            case Array ignored    -> null;
        };
    }

    /** Human-readable detail string for a {@code type_coercion_failed} violation. */
    private static String typePrecheckDetail(TypeExpression t) {
        return switch (t) {
            case Primitive p -> switch (p) {
                case INTEGER   -> "value is not a valid integer";
                case FLOAT     -> "value is not a valid float";
                case BOOLEAN   -> "value is not a valid boolean";
                case DATE      -> "value is not a valid date (expected YYYY-MM-DD)";
                case TIMESTAMP -> "value is not a valid timestamp (expected ISO 8601)";
                case TEXT      -> "type coercion failed";
            };
            case Vector v  -> "value is not a jsonb array of length " + v.dim();
            case Enum e    -> "value not in enum: " + e.values();
            default        -> "type coercion failed";
        };
    }

    /**
     * Build the {@code FROM (...) AS raw} subquery for a binding.
     * Exposes {@code source_identifier} plus every source field referenced by any effective
     * mapping (explicit or implicit passthrough) as text aliases. {@code r} itself passes
     * through as {@code __raw_payload}.
     */
    private static String rawSubquery(SourceBinding b, String rowsParam) {
        var rawFields = new ArrayList<String>();
        rawFields.add("source_identifier");
        var seen = new LinkedHashSet<String>(rawFields);

        for (var slot : b.ontologyClass().effectiveSlots()) {
            var m = b.effectiveMapping(slot.name());
            for (var src : m.sourceSlot()) {
                if (seen.add(src)) {
                    rawFields.add(src);
                }
            }
        }

        var aliasLines = new ArrayList<String>();
        aliasLines.add("        r AS __raw_payload");
        for (var f : rawFields) {
            aliasLines.add("        (r->>'" + f + "') AS " + f);
        }
        String aliases = String.join(",\n", aliasLines);
        return "FROM (\n"
                + "    SELECT\n"
                + aliases + "\n"
                + "    FROM jsonb_array_elements(%(" + rowsParam + ")s::jsonb) AS r\n"
                + ") AS raw";
    }

    /**
     * Build the full INSERT … SELECT … FROM raw … ON CONFLICT DO UPDATE upsert statement.
     */
    private static String emitClassUpsert(SourceBinding b, WriteOptions opts, String rowsParam) {
        var cls = b.ontologyClass();
        checkConcrete(cls);
        String table = bindingsTableId(cls, opts.schema(), opts.bindingsSuffix());
        String sourceLiteral = sqlLiteral(b.source().name());
        var effSlots = cls.effectiveSlots();
        String identName = cls.identifierSlot().name();

        // INSERT column list
        var insertCols = new ArrayList<String>();
        insertCols.add("source_name");
        insertCols.add("source_identifier");
        for (var slot : effSlots) insertCols.add(slot.name());
        insertCols.add("raw_payload");
        String columnsCsv = String.join(", ", insertCols);

        String rawSubq = rawSubquery(b, rowsParam);

        // SELECT lines
        var selectLines = new ArrayList<String>();
        selectLines.add("    " + sourceLiteral);
        selectLines.add("    raw.source_identifier");
        for (var slot : effSlots) {
            var m = b.effectiveMapping(slot.name());
            if (m.sql() != null) {
                selectLines.add("    " + m.sql());
            } else {
                selectLines.add("    " + passthroughValue(slot, m.sourceSlot().get(0)));
            }
        }
        selectLines.add("    raw.__raw_payload");

        // ON CONFLICT DO UPDATE — preserve canonical_id + er_metadata.
        var updateLines = new ArrayList<String>();
        for (var slot : effSlots) {
            if (!slot.name().equals(identName)) {
                updateLines.add("  " + slot.name() + " = EXCLUDED." + slot.name());
            }
        }
        updateLines.add("  raw_payload = EXCLUDED.raw_payload");
        String updateBlock = String.join(",\n", updateLines);

        return "INSERT INTO " + table + " (" + columnsCsv + ")\n"
                + "SELECT\n"
                + String.join(",\n", selectLines) + "\n"
                + rawSubq + "\n"
                + "ON CONFLICT (source_name, source_identifier) DO UPDATE SET\n"
                + updateBlock + ";";
    }

    // -------------------------------------------------------------------------
    // Internal record for fan-out grouping
    // -------------------------------------------------------------------------

    /** Groups multiple FK slots from the same referencing class into one UPDATE CTE. */
    private static final class RefClsEntry {
        final OntologyClass cls;
        final List<Slot> slots = new ArrayList<>();

        RefClsEntry(OntologyClass cls) {
            this.cls = cls;
        }
    }
}
