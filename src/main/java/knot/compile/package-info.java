/**
 * Compile — pure spec → SQL emitters. Each emitter is a free static
 * method that takes a {@link knot.spec.Spec} (and per-emitter
 * configuration kwargs) and returns SQL strings the host runs against
 * postgres.
 *
 * <p>Planned modules (mirror the Python prototype on
 * {@code library/v0}):
 * <ul>
 *   <li>{@code ResolverEmitter#emitResolvedView} — per-class
 *       {@code CREATE VIEW <class>_resolved} argmaxing across
 *       currently-open bindings via each source's accuracy.</li>
 *   <li>{@code ConstraintsEmitter#emitValidation} — per-constraint
 *       validation {@code SELECT}, with class/slot references
 *       rewritten via Apache Calcite to target the resolved view.</li>
 *   <li>{@code BindingsEmitter#emitBatchWrite} — bulk close-out +
 *       insert {@code INSERT … SELECT FROM jsonb_array_elements(…)},
 *       with optional in-transaction enforcement of error-severity
 *       constraints (PL/pgSQL {@code DO} block + {@code RAISE
 *       EXCEPTION}).</li>
 *   <li>{@code SpecIoEmitter} — save / load a {@code Spec} through the
 *       {@code knot_meta.*} tables.</li>
 *   <li>{@code MetaDdl} — invariant DDL for the meta-tables.</li>
 * </ul>
 *
 * <p>Entity-table DDL ({@code CREATE TABLE}), FK constraints, indexes,
 * and migrations are NOT emitted by knot — they live in the host's
 * jOOQ / Flyway layer.
 */
package knot.compile;
