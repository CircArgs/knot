/**
 * Canonical type API for knot specs.
 *
 * <p>The one way to declare slot types — no enum direct access, no string shorthand,
 * no separate FK method. Everything funnels through this package:
 *
 * <ul>
 *   <li>{@link knot.ast.types.Primitive#TEXT TEXT}, {@code INTEGER}, {@code FLOAT},
 *       {@code BOOLEAN}, {@code DATE}, {@code TIMESTAMP} — primitive scalars</li>
 *   <li>{@link knot.ast.types.Array Array} — homogeneous array of another
 *       {@link knot.ast.types.TypeExpression}</li>
 *   <li>{@link knot.ast.types.Vector Vector} — pgvector dense embedding with a
 *       configurable distance metric</li>
 *   <li>{@link knot.ast.types.Enum Enum} — closed set of allowed text values, stored
 *       as {@code TEXT} with an inline {@code CHECK} constraint</li>
 *   <li>{@link knot.ast.types.ClassRef ClassRef} — FK reference to another
 *       {@code OntologyClass}; the spec layer auto-wraps a bare class passed to
 *       {@code Slot} construction</li>
 * </ul>
 *
 * <p>The five record / enum implementors of {@link knot.ast.types.TypeExpression}
 * are the internal AST representation, defined here and imported by every layer
 * that needs them (the spec layer, the compile emitters). This package is the
 * canonical home; nothing else <em>defines</em> them.
 */
package knot.ast.types;
