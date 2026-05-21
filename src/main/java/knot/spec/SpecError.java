package knot.spec;

/**
 * Raised by {@link Spec#validate()} when cross-entity well-formedness fails.
 *
 * <p>Extends {@link IllegalArgumentException} so callers that catch the
 * general "bad input" shape pick this up naturally; the more specific type
 * lets tests assert on the validation path specifically.
 */
public class SpecError extends IllegalArgumentException {

    public SpecError(String message) {
        super(message);
    }

    public SpecError(String message, Throwable cause) {
        super(message, cause);
    }
}
