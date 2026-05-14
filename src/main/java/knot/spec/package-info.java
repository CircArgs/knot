/**
 * Metaschema — typed records + sealed interfaces describing a knot
 * spec. No SQL emission lives here; this package is pure data.
 *
 * <p>Planned shape (mirrors the Python prototype on {@code library/v0}):
 * <ul>
 *   <li>{@code TypeExpression} — sealed interface permitting
 *       {@code Primitive} (enum), {@code ArrayType} (record), and
 *       {@code ClassRef} (record).</li>
 *   <li>{@code AnyClass} — sealed interface permitting
 *       {@code OntologyClass} (record) and {@code VirtualClass}
 *       (record).</li>
 *   <li>{@code Slot}, {@code Constraint}, {@code Source},
 *       {@code SourceBinding}, {@code Spec} — records.</li>
 *   <li>{@code ClassKind}, {@code Severity} — enums.</li>
 * </ul>
 */
package knot.spec;
