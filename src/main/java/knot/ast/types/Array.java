package knot.ast.types;

/**
 * Homogeneous array of another {@link TypeExpression}.
 *
 * <p>Mirrors the Python {@code Array(of=…)} dataclass. The Python {@code __post_init__}
 * coerced a bare {@code OntologyClass} into a {@link ClassRef}; that auto-wrap lives in
 * the spec layer's {@code Slot} construction path instead — by the time a Java
 * {@code Array} is constructed the {@code of} must already be a {@link TypeExpression}.
 */
public record Array(TypeExpression of) implements TypeExpression {

    public Array {
        if (of == null) {
            throw new IllegalArgumentException("Array.of must be non-null");
        }
    }

    @Override
    public String toString() {
        return "array<" + of + ">";
    }
}
