package knot.compile;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;

import knot.ast.expr.FkRef;
import knot.ast.expr.IsNull;
import knot.ast.select.Layer;
import knot.ast.types.ClassRef;
import knot.spec.ClassKind;
import knot.spec.Constraint;
import knot.spec.OntologyClass;
import knot.spec.Severity;
import knot.spec.Slot;
import knot.spec.Spec;

/**
 * Constraint validation SELECT emitter.
 *
 * <p>Turns each {@link Constraint#body()} (an {@code Expr} from the semantic builder) into a
 * SELECT that surfaces rows of the primary class which violate the predicate.
 *
 * <p>Each emitted SELECT has the uniform 5-column shape:
 * <pre>
 *   SELECT
 *       '<rule_id>'    AS rule_id,
 *       '<class_name>' AS class_name,
 *       '<severity>'   AS severity,
 *       <message>      AS message,
 *       <pk_col>       AS offending_pk
 *   FROM <schema>.<class><layer>
 *   WHERE NOT (<body_sql>);
 * </pre>
 *
 * <p>Empty result → constraint passes. Non-empty rows are violations; the host inspects
 * {@code severity} to decide block-vs-warn.
 *
 * <p>Ports {@code knot/compile/constraints.py}.
 */
public final class Constraints {

    private Constraints() {}

    // -------------------------------------------------------------------------
    // Public records
    // -------------------------------------------------------------------------

    /**
     * One (name, sql) pair — a constraint name paired with the validation SELECT that returns
     * violations. Mirrors the Python {@code (str, str)} tuple.
     */
    public record NamedSql(String name, String sql) {}

    /**
     * Options for {@link #emitValidation} and {@link #emitValidationUnion}.
     *
     * @param schema                    postgres schema (default {@code "knot_data"})
     * @param layer                     which view to validate against (default {@link Layer#RESOLVED})
     * @param scopeToSourceIdentifiers  when non-null, restrict each SELECT to canonical_ids
     *                                  touched by the given (source_name → source_identifiers)
     *                                  batch; {@code null} means full-table validation
     * @param includeBuiltins           when {@code true}, also emit structural invariants derived
     *                                  from the spec shape (FK-orphan + required-null checks)
     */
    public record ConstraintsOptions(
            String schema,
            Layer layer,
            Map<String, List<String>> scopeToSourceIdentifiers,
            boolean includeBuiltins) {

        /** Python kwarg defaults. */
        public static ConstraintsOptions defaults() {
            return new ConstraintsOptions("knot_data", Layer.RESOLVED, null, true);
        }
    }

    // -------------------------------------------------------------------------
    // Built-in constraints
    // -------------------------------------------------------------------------

    /**
     * Yield invariants knot ships automatically from the spec's shape.
     *
     * <p>Two families, both prefixed {@code _builtin_}:
     * <ol>
     *   <li>{@code _builtin_fk_orphan_<Class>_<slot>} — every ClassRef slot. Body:
     *       {@code slot.isNull() | slot.targetExists()}. Severity ERROR.</li>
     *   <li>{@code _builtin_required_null_<Class>_<slot>} — every required non-identifier slot.
     *       Body: {@code slot.isNotNull()}. Severity WARNING.</li>
     * </ol>
     *
     * <p>Virtual classes are skipped — built-ins only apply to concrete {@link OntologyClass}.
     */
    public static List<Constraint> builtinConstraints(Spec spec) {
        var out = new ArrayList<Constraint>();
        for (var raw : spec.classes().values()) {
            if (!(raw instanceof OntologyClass cls) || cls.kind() != ClassKind.CONCRETE) {
                continue;
            }
            for (Slot slot : cls.effectiveSlots()) {
                if (slot.type() instanceof ClassRef cr) {
                    var fkRef = new FkRef(cls.name(), slot.name(), cr.target().name());
                    var isNull = new IsNull(fkRef, false);
                    var targetExists = fkRef.targetExists();
                    var body = isNull.or_(targetExists);
                    String msg = cls.name() + "." + slot.name() + " → "
                            + cr.target().name() + ": FK value does not match any canonical_id";
                    out.add(new Constraint(
                            "_builtin_fk_orphan_" + cls.name() + "_" + slot.name(),
                            cls,
                            body,
                            Severity.ERROR,
                            msg));
                }
                if (slot.required() && !slot.identifier()) {
                    var slotRef = cls.col().get(slot.name());
                    var body = slotRef.isNotNull();
                    String msg = "required slot " + cls.name() + "." + slot.name()
                            + " resolved to NULL (no source provided a non-null value)";
                    out.add(new Constraint(
                            "_builtin_required_null_" + cls.name() + "_" + slot.name(),
                            cls,
                            body,
                            Severity.WARNING,
                            msg));
                }
            }
        }
        return out;
    }

    // -------------------------------------------------------------------------
    // Primary emitters
    // -------------------------------------------------------------------------

    /**
     * Return {@code (constraint_name, validation_sql)} pairs — one per constraint.
     *
     * <p>Each SQL returns zero rows when the constraint holds and one row per violating
     * canonical_id otherwise.
     *
     * <p>Mirrors the Python {@code emit_validation(spec, *, schema, layer,
     * scope_to_source_identifiers, include_builtins)}.
     *
     * <p>This overload is called by {@link Spec#emitValidation(Map, boolean)}.
     */
    public static List<Spec.ConstraintEntry> emitValidation(
            Spec spec,
            Map<String, List<String>> scopeToSourceIdentifiers,
            boolean includeBuiltins) {
        var opts = new ConstraintsOptions(
                spec.schema(),
                Layer.RESOLVED,
                scopeToSourceIdentifiers,
                includeBuiltins);
        var pairs = emitValidation(spec, opts);
        var out = new ArrayList<Spec.ConstraintEntry>(pairs.size());
        for (var p : pairs) {
            out.add(new Spec.ConstraintEntry(p.name(), p.sql()));
        }
        return out;
    }

    /**
     * Return {@link NamedSql} pairs — one per constraint, using explicit {@link ConstraintsOptions}.
     */
    public static List<NamedSql> emitValidation(Spec spec, ConstraintsOptions opts) {
        String scopeSql = buildScopeSql(opts.scopeToSourceIdentifiers());

        var all = new ArrayList<Constraint>(spec.constraints());
        if (opts.includeBuiltins()) {
            all.addAll(builtinConstraints(spec));
        }

        var out = new ArrayList<NamedSql>(all.size());
        for (var c : all) {
            out.add(new NamedSql(c.name(), buildSql(c, opts.schema(), opts.layer(), scopeSql)));
        }
        return out;
    }

    /**
     * Return a single {@code UNION ALL} of every constraint's validation SELECT, or {@code null}
     * when the spec has no constraints.
     *
     * <p>Mirrors Python {@code emit_validation_union}.
     */
    public static String emitValidationUnion(Spec spec, ConstraintsOptions opts) {
        var parts = emitValidation(spec, opts);
        if (parts.isEmpty()) {
            return null;
        }
        var sb = new StringBuilder();
        for (int i = 0; i < parts.size(); i++) {
            if (i > 0) sb.append("\nUNION ALL\n");
            // Strip trailing semicolon from each individual SELECT.
            String sql = parts.get(i).sql();
            if (sql.endsWith(";")) {
                sql = sql.substring(0, sql.length() - 1);
            }
            sb.append(sql);
        }
        sb.append(";");
        return sb.toString();
    }

    // -------------------------------------------------------------------------
    // Private helpers
    // -------------------------------------------------------------------------

    private static String escapeLiteral(String s) {
        return s.replace("'", "''");
    }

    /**
     * Build the {@code (source_name, source_identifier) IN (…)} pairs SQL fragment, or
     * {@code null} when {@code scope} is null or empty.
     */
    private static String buildScopeSql(Map<String, List<String>> scope) {
        if (scope == null || scope.isEmpty()) {
            return null;
        }
        var pairs = new ArrayList<String>();
        for (var entry : scope.entrySet()) {
            String srcLit = "'" + escapeLiteral(entry.getKey()) + "'";
            for (String ident : entry.getValue()) {
                String identLit = "'" + escapeLiteral(ident) + "'";
                pairs.add("(" + srcLit + ", " + identLit + ")");
            }
        }
        if (pairs.isEmpty()) {
            return null;
        }
        return String.join(", ", pairs);
    }

    /** Build one validation SELECT for constraint {@code c}. */
    private static String buildSql(Constraint c, String schema, Layer layer, String scopeSql) {
        OntologyClass primary = c.primary();
        Slot identifier = primary.identifierSlot();
        String table = schema + "." + primary.name().toLowerCase() + layer.suffix();
        String bindingsTable = schema + "." + primary.name().toLowerCase() + "_bindings";
        String messageLiteral = c.message() != null
                ? "'" + escapeLiteral(c.message()) + "'"
                : "NULL";

        String bodySql = ExprCompiler.compileSql(c.body(), schema, layer, primary.name());

        String scopeClause = "";
        if (scopeSql != null) {
            scopeClause = "\n  AND " + table + "." + identifier.name() + " IN (\n"
                    + "    SELECT DISTINCT canonical_id FROM " + bindingsTable + "\n"
                    + "    WHERE (source_name, source_identifier) IN (" + scopeSql + ")\n"
                    + "      AND canonical_id IS NOT NULL\n"
                    + "  )";
        }

        return "SELECT\n"
                + "    '" + escapeLiteral(c.name()) + "' AS rule_id,\n"
                + "    '" + escapeLiteral(primary.name()) + "' AS class_name,\n"
                + "    '" + escapeLiteral(c.severity().value()) + "' AS severity,\n"
                + "    " + messageLiteral + " AS message,\n"
                + "    " + identifier.name() + " AS offending_pk\n"
                + "FROM " + table + "\n"
                + "WHERE NOT (" + bodySql + ")" + scopeClause + ";";
    }
}
