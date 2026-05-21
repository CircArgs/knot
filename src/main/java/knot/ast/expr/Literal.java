package knot.ast.expr;

/**
 * A constant value — {@code String}, {@code Integer}, {@code Long},
 * {@code Double}, {@code Boolean}, {@code null}, {@code List<?>}, etc.
 *
 * <p>The Python source uses {@code Any}; in Java the value type is {@code Object}
 * so any reference is acceptable. {@code null} is a valid {@code value} (renders
 * as {@code NULL}); construction does not validate the payload — type-checking
 * happens at compile time when the surrounding column type is known.
 */
public record Literal(Object value) implements ValueExpr {
}
