package knot.ast.expr;

import java.util.Set;

/**
 * Set-aggregate over an implicit row-set.
 *
 * <p>The "implicit row-set" is defined by {@code predicate}: rows of the class
 * whose slot refs appear in the predicate. {@code kind} picks the aggregation:
 *
 * <ul>
 *   <li>{@code "any"}   → {@code EXISTS (SELECT 1 …)} — boolean</li>
 *   <li>{@code "none"}  → {@code NOT EXISTS (…)} — boolean</li>
 *   <li>{@code "count"} → {@code (SELECT COUNT(*) …)} — value (comparable)</li>
 *   <li>{@code "all"}   → {@code NOT EXISTS (… AND NOT condition)} — boolean,
 *       vacuously true on empty set; {@code condition} is required.</li>
 * </ul>
 *
 * <p>Implements {@link ValueExpr} so {@code count()} works in comparisons
 * ({@code .count().gt(5)}, {@code .count().eq(0)}). Using comparison
 * operators on a boolean-kind aggregate ({@code .any_().gt(5)}) is nonsense
 * SQL — not enforced at the type level; surfaces as opaque postgres.
 */
public record Aggregate(String kind, Expr predicate, Expr condition) implements ValueExpr {

    private static final Set<String> VALID_KINDS = Set.of("any", "none", "count", "all");

    public Aggregate {
        if (!VALID_KINDS.contains(kind)) {
            throw new IllegalArgumentException(
                    "Aggregate.kind must be one of any/none/count/all, got '" + kind + "'");
        }
        if (predicate == null) {
            throw new IllegalArgumentException("Aggregate.predicate must be non-null");
        }
        if ("all".equals(kind) && condition == null) {
            throw new IllegalArgumentException("Aggregate.all requires a condition");
        }
        if (!"all".equals(kind) && condition != null) {
            throw new IllegalArgumentException(
                    "Aggregate.condition only used with kind='all', not '" + kind + "'");
        }
    }
}
