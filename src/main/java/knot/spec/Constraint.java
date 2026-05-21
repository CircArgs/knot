package knot.spec;

import knot.ast.expr.Expr;

/**
 * Cross-row / cross-class invariant — body is an {@link Expr} from the
 * semantic builder, not raw SQL.
 *
 * <p>Mirrors the Python {@code @dataclass(slots=True) Constraint}. The
 * Python dataclass is mutable in shape (StrEnum coercion happens in
 * {@code __post_init__}); Java handles severity coercion via the
 * {@link #of(String, OntologyClass, Expr, String, String)} static factory
 * that takes a string severity, leaving the canonical record constructor
 * strongly typed.
 */
public record Constraint(
        String name,
        OntologyClass primary,
        Expr body,
        Severity severity,
        String message) {

    public Constraint {
        Names.checkName("Constraint", name);
        if (primary == null) {
            throw new IllegalArgumentException(
                    "Constraint '" + name + "'.primary must be non-null");
        }
        if (body == null) {
            throw new IllegalArgumentException(
                    "Constraint '" + name + "'.body must be non-null (use the builder: "
                            + "e.g. movie.col(\"year\").ge(1888))");
        }
        if (severity == null) {
            severity = Severity.ERROR;
        }
    }

    /** Canonical constructor with default {@link Severity#ERROR} and no message. */
    public static Constraint of(String name, OntologyClass primary, Expr body) {
        return new Constraint(name, primary, body, Severity.ERROR, null);
    }

    /** Static factory accepting severity as a string (parsed via {@link Severity#fromValue}). */
    public static Constraint of(
            String name, OntologyClass primary, Expr body, String severity, String message) {
        return new Constraint(name, primary, body, Severity.fromValue(severity), message);
    }
}
