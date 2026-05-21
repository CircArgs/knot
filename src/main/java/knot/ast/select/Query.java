package knot.ast.select;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Objects;
import java.util.Set;
import knot.ast.expr.Expr;
import knot.compile.QueryCompiler;
import knot.spec.Spec;

/**
 * A SELECT-shape query over one primary class.
 *
 * <p>Frozen-equivalent: every builder ({@code .where}, {@code .groupBy}, {@code .orderBy},
 * {@code .limit}, {@code .offset}, {@code .select}, {@code .lock}, {@code .withSpec}) returns a
 * new {@code Query}. The Python AST mutated nothing; the Java port keeps the same posture.
 *
 * <p>{@code specRef} is a back-reference set by the class-side entry points
 * ({@code OntologyClass.resolved()} / {@code .allSources()} / {@code .fromSource(...)} /
 * {@code .unresolved()}) so {@code q.sql()} knows which spec to compile against. It's
 * intentionally excluded from {@link #equals(Object)} / {@link #hashCode()} so the AST still
 * behaves like pure data for tests — two queries with the same shape compare equal regardless
 * of which spec they reference.
 *
 * <p>{@code whereClause}, {@code limitValue}, {@code offsetValue}, {@code projection},
 * {@code lockMode}, and {@code specRef} are nullable. {@code grouping} and {@code ordering}
 * are always non-null lists (possibly empty) and held as immutable copies.
 */
public record Query(
        String className,
        Expr whereClause,
        List<Expr> grouping,
        List<OrderBy> ordering,
        Integer limitValue,
        Integer offsetValue,
        List<Expr> projection,
        Layer layer,
        String lockMode,
        Spec specRef) {

    private static final Set<String> VALID_LOCK_MODES =
            Set.of("for_update", "for_update_skip_locked", "for_share");

    public Query {
        if (className == null || className.isBlank()) {
            throw new IllegalArgumentException("Query.className must be non-blank");
        }
        if (layer == null) {
            throw new IllegalArgumentException("Query.layer must be non-null");
        }
        // Defensive copies; never accept null lists.
        grouping = grouping == null ? List.of() : List.copyOf(grouping);
        ordering = ordering == null ? List.of() : List.copyOf(ordering);
        // projection: null means SELECT *; an empty list also means SELECT *.
        if (projection != null) {
            projection = List.copyOf(projection);
        }
    }

    /**
     * Minimal constructor — matches the Python default field values. Defaults
     * {@code layer = RESOLVED}, every collection empty, no spec back-reference.
     */
    public Query(String className) {
        this(className, null, List.of(), List.of(), null, null, null, Layer.RESOLVED, null, null);
    }

    // ------------------------------------------------------------------
    // Fluent builders — every method returns a new Query.
    // ------------------------------------------------------------------

    /** AND {@code predicate} into the existing WHERE clause. */
    public Query where(Expr predicate) {
        if (predicate == null) {
            throw new IllegalArgumentException("Query.where predicate must be non-null");
        }
        Expr combined = (whereClause == null) ? predicate : whereClause.and_(predicate);
        return new Query(
                className, combined, grouping, ordering, limitValue, offsetValue, projection,
                layer, lockMode, specRef);
    }

    /**
     * Append GROUP BY clauses. Pair with aggregate projections ({@code count()} / {@code sum()}
     * / {@code avg()} / etc. from {@code knot.ast.expr}). Without {@code groupBy}, an aggregate
     * in {@code .select()} reduces the result to one scalar row.
     */
    public Query groupBy(Expr... refs) {
        List<Expr> next = new ArrayList<>(grouping);
        Collections.addAll(next, refs);
        return new Query(
                className, whereClause, next, ordering, limitValue, offsetValue, projection,
                layer, lockMode, specRef);
    }

    /** Append an ORDER BY clause (defaults to {@code "asc"}). */
    public Query orderBy(Expr ref) {
        return orderBy(ref, "asc");
    }

    /** Append an ORDER BY clause. */
    public Query orderBy(Expr ref, String direction) {
        List<OrderBy> next = new ArrayList<>(ordering);
        next.add(new OrderBy(ref, direction));
        return new Query(
                className, whereClause, grouping, next, limitValue, offsetValue, projection,
                layer, lockMode, specRef);
    }

    public Query limit(int n) {
        return new Query(
                className, whereClause, grouping, ordering, n, offsetValue, projection,
                layer, lockMode, specRef);
    }

    public Query offset(int n) {
        return new Query(
                className, whereClause, grouping, ordering, limitValue, n, projection,
                layer, lockMode, specRef);
    }

    /** Set the projection. A null/empty projection means {@code SELECT *}. */
    public Query select(Expr... refs) {
        List<Expr> next = List.of(refs);
        return new Query(
                className, whereClause, grouping, ordering, limitValue, offsetValue, next,
                layer, lockMode, specRef);
    }

    /**
     * Append a row-lock clause. Postgres modes:
     *
     * <ul>
     *   <li>{@code "for_update"} — exclusive row lock
     *   <li>{@code "for_update_skip_locked"} — exclusive lock, skip rows already locked by
     *       another transaction. The canonical ER worker shape: claim a batch of
     *       {@code cls.unresolved()} rows atomically without blocking on or re-processing rows
     *       another worker has already claimed.
     *   <li>{@code "for_share"} — shared row lock
     * </ul>
     *
     * <p>Renders after LIMIT / OFFSET (postgres clause order).
     */
    public Query lock(String mode) {
        if (!VALID_LOCK_MODES.contains(mode)) {
            throw new IllegalArgumentException(
                    "Query.lock mode must be one of "
                            + VALID_LOCK_MODES.stream().sorted().toList()
                            + ", got '"
                            + mode
                            + "'");
        }
        return new Query(
                className, whereClause, grouping, ordering, limitValue, offsetValue, projection,
                layer, mode, specRef);
    }

    // ------------------------------------------------------------------
    // Compile entry point — methods on the entity they're about.
    // ------------------------------------------------------------------

    /**
     * Compile this query to a postgres SQL string against its owning spec — schema name comes
     * from the spec. Literals are inlined; no positional-parameter list to bind. No validation
     * (hot path). Throws {@link IllegalStateException} if this query wasn't built via a spec's
     * class (no back-reference).
     */
    public String sql() {
        if (specRef == null) {
            throw new IllegalStateException(
                    "Query has no spec back-reference — build it via cls.resolved() / "
                            + ".allSources() / .fromSource(...) / .unresolved(), or call "
                            + "Query.withSpec(spec) first");
        }
        return QueryCompiler.compileQuery(this, specRef, specRef.schema());
    }

    /**
     * Return a copy of this query bound to {@code spec}. Useful when the AST was constructed
     * directly ({@code new Query("Movie")}) and you want to attach a spec after the fact.
     */
    public Query withSpec(Spec spec) {
        return new Query(
                className, whereClause, grouping, ordering, limitValue, offsetValue, projection,
                layer, lockMode, spec);
    }

    // ------------------------------------------------------------------
    // Equality — exclude specRef so two queries with the same shape but
    // different (or absent) back-references still compare equal. Mirrors
    // the Python ``compare=False`` on the ``_spec`` field.
    // ------------------------------------------------------------------

    @Override
    public boolean equals(Object o) {
        if (this == o) return true;
        if (!(o instanceof Query other)) return false;
        return Objects.equals(className, other.className)
                && Objects.equals(whereClause, other.whereClause)
                && Objects.equals(grouping, other.grouping)
                && Objects.equals(ordering, other.ordering)
                && Objects.equals(limitValue, other.limitValue)
                && Objects.equals(offsetValue, other.offsetValue)
                && Objects.equals(projection, other.projection)
                && layer == other.layer
                && Objects.equals(lockMode, other.lockMode);
    }

    @Override
    public int hashCode() {
        return Objects.hash(
                className,
                whereClause,
                grouping,
                ordering,
                limitValue,
                offsetValue,
                projection,
                layer,
                lockMode);
    }
}
