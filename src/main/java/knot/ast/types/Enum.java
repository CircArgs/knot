package knot.ast.types;

import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Set;

/**
 * Closed set of allowed text values for a slot.
 *
 * <p>Stored as {@code TEXT} in postgres with an inline {@code CHECK} constraint.
 * No {@code CREATE TYPE} ceremony — the constraint is self-contained in the
 * {@code CREATE TABLE}, so adding/removing values is a simple
 * {@code ALTER TABLE … ALTER COLUMN … TYPE … USING …} (or migrate-tool diff) with no
 * separate type object to manage.
 *
 * <p>The {@code values} list is defensively copied and wrapped in
 * {@link List#copyOf(java.util.Collection)} so the record stays effectively immutable.
 */
public record Enum(List<String> values) implements TypeExpression {

    public Enum {
        if (values == null || values.isEmpty()) {
            throw new IllegalArgumentException("ENUM requires at least one value");
        }
        Set<String> seen = new HashSet<>();
        List<String> dupes = new ArrayList<>();
        for (String v : values) {
            if (v == null) {
                throw new IllegalArgumentException("ENUM values must all be non-null strings");
            }
            if (!seen.add(v)) {
                dupes.add(v);
            }
        }
        if (!dupes.isEmpty()) {
            throw new IllegalArgumentException(
                    "ENUM values must be unique; duplicates: " + dupes);
        }
        values = List.copyOf(values);
    }

    /** Static factory mirroring the Python {@code ENUM(*values)} convenience. */
    public static Enum of(String... values) {
        if (values == null) {
            throw new IllegalArgumentException("ENUM requires at least one value");
        }
        return new Enum(List.of(values));
    }

    @Override
    public String toString() {
        var sb = new StringBuilder("enum(");
        for (int i = 0; i < values.size(); i++) {
            if (i > 0) {
                sb.append(", ");
            }
            sb.append('\'').append(values.get(i)).append('\'');
        }
        return sb.append(')').toString();
    }
}
