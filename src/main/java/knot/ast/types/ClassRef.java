package knot.ast.types;

import knot.spec.OntologyClass;

/**
 * FK reference to another class — stored as the target's {@code canonical_id}.
 *
 * <p>Mirrors the Python {@code ClassRef(target=…)} dataclass. {@code OntologyClass}
 * is owned by the {@code knot.spec} module; this record references it by name so the
 * integration pass at the end of the port resolves the cross-package link.
 */
public record ClassRef(OntologyClass target) implements TypeExpression {

    public ClassRef {
        if (target == null) {
            throw new IllegalArgumentException("ClassRef.target must be non-null");
        }
    }

    @Override
    public String toString() {
        return target.name();
    }
}
