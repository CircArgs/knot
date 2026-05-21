package knot.spec;

/**
 * Constraint violation severity.
 *
 * <p>{@link #ERROR} blocks writes; {@link #WARNING} is reported but does not
 * block. {@link #INFO} is informational only.
 *
 * <p>Mirrors the Python {@code Severity(StrEnum)}. The {@link #value()}
 * accessor returns the lower-case string the Python {@code StrEnum} carried.
 */
public enum Severity {
    ERROR("error"),
    WARNING("warning"),
    INFO("info");

    private final String value;

    Severity(String value) {
        this.value = value;
    }

    /** Lower-case string — what the Python {@code StrEnum} member's value was. */
    public String value() {
        return value;
    }

    /** Parse from the lower-case string spelling. */
    public static Severity fromValue(String s) {
        if (s == null) {
            throw new IllegalArgumentException("Severity value must be non-null");
        }
        for (var v : values()) {
            if (v.value.equals(s)) {
                return v;
            }
        }
        throw new IllegalArgumentException(
                "Severity must be one of [error, warning, info]; got '" + s + "'");
    }
}
