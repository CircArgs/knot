package knot.spec;

import knot.ast.types.ClassRef;
import knot.ast.types.TypeExpression;

/**
 * A property of an {@link OntologyClass} — primitive, array, vector, enum, or FK.
 *
 * <p>Mirrors the Python {@code @dataclass(slots=True) Slot}. {@code description}
 * may be {@code null} when no docstring is attached. The compact constructor
 * validates the name and silently coerces {@code identifier=true} to imply
 * {@code required=true} (an identifier is NOT NULL by definition).
 *
 * <p>Python's positional-keyword convention is reproduced here via the
 * canonical constructor order: {@code (name, type, required, identifier,
 * description)}. The static factory {@link #of(String, TypeExpression)}
 * mirrors the Python default of {@code required=false, identifier=false,
 * description=null}.
 */
public record Slot(
        String name,
        TypeExpression type,
        boolean required,
        boolean identifier,
        String description) {

    public Slot {
        Names.checkName("Slot", name);
        if (type == null) {
            throw new IllegalArgumentException("Slot.type must be non-null");
        }
        // Identifier is by definition required (NOT NULL on the bindings table).
        // Coerce silently — common builder mistake.
        if (identifier && !required) {
            required = true;
        }
    }

    /** Bare slot — {@code required=false}, {@code identifier=false}, no description. */
    public static Slot of(String name, TypeExpression type) {
        return new Slot(name, type, false, false, null);
    }

    /** {@code true} iff this slot's type is a {@link ClassRef} (FK to another class). */
    public boolean isFk() {
        return type instanceof ClassRef;
    }
}
