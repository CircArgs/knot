package knot.ast.types;

/**
 * Closed union of slot type expressions.
 *
 * <p>Mirrors the Python {@code TypeExpression = Primitive | Array | ClassRef | Vector | Enum}.
 * The five permitted implementors are the only legal slot type shapes; the spec layer
 * funnels every user-facing constructor (TEXT, ARRAY, VECTOR, ENUM, and FK auto-wrap)
 * through this surface.
 */
public sealed interface TypeExpression
        permits Primitive, Array, Vector, ClassRef, Enum {
}
