package knot.spec;

import java.util.regex.Pattern;

/**
 * Shared helpers for entity-name validation.
 *
 * <p>Package-private — every spec entity ({@link Slot}, {@link OntologyClass},
 * {@link VirtualClass}, {@link Source}, {@link Constraint}) funnels through
 * {@link #checkName(String, String)} so the regex lives in one place.
 *
 * <p>Mirrors the Python {@code _check_name} helper.
 */
final class Names {

    private static final Pattern NAME_PATTERN = Pattern.compile("^[A-Za-z_][A-Za-z0-9_]*$");

    static final String NAME_PATTERN_SOURCE = "^[A-Za-z_][A-Za-z0-9_]*$";

    private Names() {}

    /** Raises {@link IllegalArgumentException} on empty / null / pattern-mismatched names. */
    static void checkName(String kind, String name) {
        if (name == null || name.isEmpty()) {
            throw new IllegalArgumentException(
                    kind + " name must be a non-empty string, got " + repr(name));
        }
        if (!NAME_PATTERN.matcher(name).matches()) {
            throw new IllegalArgumentException(
                    kind + " name " + repr(name) + " must match " + NAME_PATTERN_SOURCE
                            + " (letters, digits, underscores; starts with letter or underscore)");
        }
    }

    private static String repr(String s) {
        return s == null ? "null" : "'" + s + "'";
    }
}
