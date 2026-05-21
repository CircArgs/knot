package knot.ast.types;

/**
 * Closed set of primitive scalar types.
 *
 * <p>The enum value names mirror the Python {@code StrEnum} members; the lower-case SQL
 * spelling is held in {@link #sqlName()} for compile-time emitters.
 */
public enum Primitive implements TypeExpression {
    TEXT("text"),
    INTEGER("integer"),
    FLOAT("float"),
    BOOLEAN("boolean"),
    DATE("date"),
    TIMESTAMP("timestamp");

    private final String sqlName;

    Primitive(String sqlName) {
        this.sqlName = sqlName;
    }

    /** Lower-case SQL spelling — what the Python {@code StrEnum} value carried. */
    public String sqlName() {
        return sqlName;
    }

    @Override
    public String toString() {
        return sqlName;
    }
}
