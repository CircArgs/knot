package knot.spec;

/**
 * Whether an {@link OntologyClass} materializes a table ({@link #CONCRETE})
 * or is mixin-only ({@link #ABSTRACT}).
 *
 * <p>Mirrors the Python {@code ClassKind(StrEnum)}. The {@link #value()}
 * accessor returns the lower-case string the Python {@code StrEnum} carried.
 */
public enum ClassKind {
    CONCRETE("concrete"),
    ABSTRACT("abstract");

    private final String value;

    ClassKind(String value) {
        this.value = value;
    }

    /** Lower-case string — what the Python {@code StrEnum} member's value was. */
    public String value() {
        return value;
    }

    /** Parse from the lower-case string spelling. */
    public static ClassKind fromValue(String s) {
        if (s == null) {
            throw new IllegalArgumentException("ClassKind value must be non-null");
        }
        for (var v : values()) {
            if (v.value.equals(s)) {
                return v;
            }
        }
        throw new IllegalArgumentException(
                "ClassKind must be one of [concrete, abstract]; got '" + s + "'");
    }
}
