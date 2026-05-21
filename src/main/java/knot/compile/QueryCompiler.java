package knot.compile;

import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.stream.Collectors;

import knot.ast.expr.Aggregate;
import knot.ast.expr.Between;
import knot.ast.expr.BoolOp;
import knot.ast.expr.Compare;
import knot.ast.expr.CountRel;
import knot.ast.expr.Exists;
import knot.ast.expr.Expr;
import knot.ast.expr.FkChainRef;
import knot.ast.expr.InList;
import knot.ast.expr.IsNull;
import knot.ast.expr.Not;
import knot.ast.select.Layer;
import knot.ast.select.Query;
import knot.spec.OntologyClass;
import knot.spec.Spec;

/**
 * Postgres SQL compilation for the read substrate.
 *
 * <p>Sibling to {@link ExprCompiler} — that one renders {@link knot.ast.expr.Expr} fragments, this
 * one renders full {@link Query} statements. Both return raw SQL strings; literal values are
 * inlined at compile time so there is no positional-parameter list to thread through to the driver.
 *
 * <p>JOIN assembly: a pre-pass walks the query AST (where / orderBy / projection) collecting every
 * {@link FkChainRef}. Each unique chain prefix (sourceClass + FK slot path) gets one aliased JOIN.
 * Two FK slots that point at the same target class get distinct aliases — e.g. {@code movie_director}
 * vs {@code movie_writer} — so postgres never sees a duplicate table reference. The alias scheme is
 * defined in {@link Aliases#chainAlias} and used identically here and in {@link ExprCompiler} so
 * column references always match the alias assigned to their JOIN.
 *
 * <p>Adding a second SQL dialect (Trino / Spark) is a new class with its own switch — same
 * open/closed flip as {@link ExprCompiler}.
 *
 * <p>Ports {@code knot/compile/query.py}.
 */
public final class QueryCompiler {

    private QueryCompiler() {}

    private static final Map<String, String> LOCK_SQL = Map.of(
            "for_update", "FOR UPDATE",
            "for_update_skip_locked", "FOR UPDATE SKIP LOCKED",
            "for_share", "FOR SHARE");

    // ------------------------------------------------------------------
    // Public entry point
    // ------------------------------------------------------------------

    /**
     * Compile {@code q} to a postgres SQL statement string.
     *
     * <p>Literals are inlined; no positional-parameter list. No validation (hot path).
     */
    public static String compileQuery(Query q, Spec spec, String schema) {
        var layer = q.layer();
        var table = "%s.%s%s".formatted(schema, q.className().toLowerCase(), layer.suffix());

        // Projection — null/empty means SELECT *.
        String selectSql;
        if (q.projection() == null || q.projection().isEmpty()) {
            selectSql = "*";
        } else {
            selectSql = q.projection().stream()
                    .map(r -> ExprCompiler.compileSql(r, schema, layer, q.className()))
                    .collect(Collectors.joining(", "));
        }

        // Collect FK chains from everywhere a Ref could appear.
        var chains = new ArrayList<FkChainRef>();
        if (q.whereClause() != null) {
            collectChains(q.whereClause(), chains);
        }
        for (var ob : q.ordering()) {
            collectChains(ob.ref(), chains);
        }
        if (q.projection() != null) {
            for (var r : q.projection()) {
                collectChains(r, chains);
            }
        }

        // Build JOIN clauses. Each chain prefix gets one aliased JOIN, deduplicated by alias
        // (= sourceClass + FK slot path). Two FK slots pointing at the same target class get
        // distinct aliases so postgres never sees a duplicate table reference.
        var seen = new LinkedHashSet<String>();
        var joins = new ArrayList<String>();
        for (var chainRef : chains) {
            // lhs tracks the left-hand side of the ON clause: the schema-qualified primary table
            // for the first hop, then the alias of the previous hop for every subsequent hop.
            var lhs = "%s.%s%s".formatted(schema, chainRef.sourceClass().toLowerCase(), layer.suffix());
            for (int i = 0; i < chainRef.chain().size(); i++) {
                var hop = chainRef.chain().get(i);
                var prefix = chainRef.chain().subList(0, i + 1);
                var alias = Aliases.chainAlias(chainRef.sourceClass(), prefix);
                if (!seen.contains(alias)) {
                    seen.add(alias);
                    var targetCls = lookupClass(spec, hop.targetClassName());
                    var targetIdent = targetCls.identifierSlot().name();
                    joins.add(
                            "JOIN %s.%s%s AS %s ON %s.%s = %s.%s".formatted(
                                    schema, hop.targetClassName().toLowerCase(), layer.suffix(), alias,
                                    alias, targetIdent,
                                    lhs, hop.fkSlotName()));
                }
                lhs = alias;
            }
        }

        var parts = new ArrayList<String>();
        parts.add("SELECT " + selectSql);
        parts.add("FROM " + table);
        parts.addAll(joins);

        if (q.whereClause() != null) {
            // outerClass is the query's primary class so Aggregate sub-predicates can resolve
            // their this.X refs.
            var whereSql = ExprCompiler.compileSql(
                    q.whereClause(), schema, layer, q.className());
            parts.add("WHERE " + whereSql);
        }

        if (!q.grouping().isEmpty()) {
            var groupParts = q.grouping().stream()
                    .map(g -> ExprCompiler.compileSql(g, schema, layer, q.className()))
                    .collect(Collectors.joining(", "));
            parts.add("GROUP BY " + groupParts);
        }

        if (!q.ordering().isEmpty()) {
            var orderParts = q.ordering().stream()
                    .map(ob -> ExprCompiler.compileSql(ob.ref(), schema, layer, q.className())
                            + " " + ob.direction().toUpperCase())
                    .collect(Collectors.joining(", "));
            parts.add("ORDER BY " + orderParts);
        }

        if (q.limitValue() != null) {
            parts.add("LIMIT " + q.limitValue());
        }

        if (q.offsetValue() != null) {
            parts.add("OFFSET " + q.offsetValue());
        }

        if (q.lockMode() != null) {
            var lockSql = LOCK_SQL.get(q.lockMode());
            if (lockSql == null) {
                throw new IllegalArgumentException("Unknown lock mode: " + q.lockMode());
            }
            parts.add(lockSql);
        }

        return String.join("\n", parts) + ";";
    }

    // ------------------------------------------------------------------
    // Private helpers
    // ------------------------------------------------------------------

    /**
     * Resolve a class name in {@code spec}.
     *
     * @throws IllegalArgumentException if absent
     */
    private static OntologyClass lookupClass(Spec spec, String name) {
        var cls = spec.classes().get(name);
        if (cls == null) {
            throw new IllegalArgumentException("class '" + name + "' not found in spec");
        }
        // spec.classes() values may be OntologyClass or VirtualClass — only OntologyClass has
        // identifierSlot(). Cast after confirming.
        if (cls instanceof OntologyClass oc) {
            return oc;
        }
        throw new IllegalArgumentException(
                "class '" + name + "' in spec is not an OntologyClass (got "
                        + cls.getClass().getSimpleName() + ")");
    }

    /**
     * Walk an {@link Expr} tree collecting every {@link FkChainRef} reached.
     *
     * <p>Deduplication happens at JOIN-emit time on the alias — not on the {@code FkChainRef}
     * itself, so multiple chains that share a prefix still produce one JOIN per shared step.
     *
     * <p>FK chains inside an {@link Aggregate} predicate are scoped to the sub-query, not the
     * outer FROM. Skip them at the outer level; nested-JOIN-in-subquery support is a later
     * iteration.
     */
    static void collectChains(Expr node, List<FkChainRef> out) {
        switch (node) {
            case FkChainRef c -> out.add(c);
            case Compare cmp -> {
                collectChains(cmp.left(), out);
                collectChains(cmp.right(), out);
            }
            case BoolOp b -> {
                collectChains(b.left(), out);
                collectChains(b.right(), out);
            }
            case Not n -> collectChains(n.expr(), out);
            case IsNull isnull -> collectChains(isnull.expr(), out);
            case InList inlist -> collectChains(inlist.left(), out);
            case Between b -> collectChains(b.left(), out);
            case Exists e -> {
                if (e.where() != null) collectChains(e.where(), out);
            }
            case CountRel cr -> {
                if (cr.where() != null) collectChains(cr.where(), out);
            }
            case Aggregate ignored -> {
                // FK chains inside an Aggregate predicate are scoped to the sub-query — skip at
                // the outer level. Nested-JOIN-in-subquery support is a later iteration.
            }
            default -> {
                // Ref / Literal / Raw / FkRef / VectorRef / VectorDistance / This / AggExpr /
                // TargetExists / TupleCompare / TupleIn have no nested chain children.
            }
        }
    }
}
