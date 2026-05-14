/**
 * knot — reflective ontology compiler (Java port, scaffold).
 *
 * <p>knot is a thin layer that compiles a typed {@code Spec} into the
 * SQL artifacts unique to the multi-source / trust-resolved domain:
 * SCD2 binding tables, per-class resolved views, constraint validation
 * SELECTs, and spec persistence. Entity-table DDL, FK constraints,
 * indexes, batch INSERT, and migrations are delegated to the host
 * substrate (jOOQ + Flyway is the canonical pair).
 *
 * <p>Subpackages:
 * <ul>
 *   <li>{@link knot.spec} — metaschema records + sealed interfaces.</li>
 *   <li>{@link knot.compile} — pure spec → SQL emitters.</li>
 * </ul>
 */
package knot;
