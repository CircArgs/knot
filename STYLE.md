# Java port — style guide

Read this before porting any Python module. The Python is the
source of truth at `/mnt/main/code/knot/knot/` (on branch
`library/v0-substrate` HEAD `bded5f2`). Switch to that branch with
`git show library/v0-substrate:knot/<file>.py` to read it. This
`java/v0` branch is the Java target.

## Layout

```
knot-java root layout:
  build.gradle.kts
  settings.gradle.kts
  src/main/java/knot/
    ast/
      types/     # TypeExpression sealed interface + Primitive enum + record impls
      expr/      # Expr sealed interface + 20+ AST node records
      select/    # Query record + OrderBy record + Layer enum
    spec/        # Spec, OntologyClass, VirtualClass, Slot, Source, SourceBinding,
                 # SlotMapping, Constraint, Severity, ClassKind, ReverseRef
    compile/     # Ddl, ExprCompiler, QueryCompiler, Write, Constraints,
                 # Weight, Explain, Resolver
  src/test/java/knot/
    ... (mirror of main/, JUnit 5 + AssertJ)
```

Package = `knot.*` (NOT `com.knot.*`). Match Python module names →
Java packages (`knot.ast.types`, `knot.compile.ddl`, etc.).

## Target

Java 21 (LTS). Use modern features:

- **Records** for every immutable data class (Python `@dataclass(frozen=True, slots=True)` → Java `record`).
- **Sealed interfaces** for closed type unions (Python `TypeExpression = Primitive | Array | Vector | …` → `sealed interface TypeExpression permits Primitive, Array, Vector, …`).
- **Switch expression pattern matching** over sealed types (replaces Python `@functools.singledispatch`).
- **Text blocks** (`"""…"""`) for multi-line SQL.
- **`var`** for local type inference where it improves readability.

## Naming

- Python `snake_case` → Java `camelCase` for methods, `PascalCase` for types.
- Python `private_with_underscore` → Java `private` keyword.
- Python `cls.col.year` accessor pattern → Java method `cls.col().get("year")` or chained record access.
- Python `_ColAccess` / `_BindingsColAccess` → package-private Java classes with public method API.

## Translation rules

| Python | Java |
|---|---|
| `@dataclass(frozen=True, slots=True)` | `record` |
| `@dataclass` (mutable) | regular class with private fields + getters (no setters unless mutation is intended; Spec / OntologyClass / SourceBinding need mutation) |
| `class Foo(Expr, _ValueExpr): ...` | `record Foo(...) implements Expr, ValueExpr {}` — Expr + ValueExpr are sealed interfaces |
| `singledispatch` | `switch (node) { case Ref r -> ...; case FkRef f -> ...; }` pattern-matching switch expression |
| `match value: case Foo(): ...` | `switch (value) { case Foo f -> ...; }` |
| `str.replace("'", "''")` | helper method `sqlLiteral(String)` in a `SqlLiterals` utility class |
| f-string | text block + `String.formatted(...)` OR `"%s...".formatted(...)` |
| Python `list[T]` | `java.util.List<T>` |
| Python `dict[K, V]` | `java.util.Map<K, V>` |
| Python `tuple[A, B]` | a record (don't use raw arrays or Pair) |
| Python `None` | `null` for optional fields; `Optional<T>` ONLY for return types that may be absent |
| Python `raise ValueError(...)` | `throw new IllegalArgumentException(...)` |
| Python `raise KeyError(...)` | `throw new IllegalArgumentException(...)` |
| Python `raise RuntimeError(...)` | `throw new IllegalStateException(...)` |
| Python `@property` | parameterless method (no `get` prefix; records auto-generate accessors) |

## Construction-time validation

Use **compact constructors** on records:

```java
public record Slot(String name, TypeExpression type, boolean required, boolean identifier) {
    public Slot {
        if (name == null || name.isBlank())
            throw new IllegalArgumentException("Slot.name must be non-blank");
    }
}
```

## Testing

- JUnit 5 (`org.junit.jupiter.api.Test`)
- AssertJ for fluent assertions
- One test class per Java type, mirror Python test layout
- SQL-shape: substring-match (`assertThat(sql).contains("..")`) — no SQL parser dep

## Skip in v0

- Notebook ports
- Integration tests against live postgres
- `knot_graphql` adapter (separate, build later)
- `notebooks/` directory entirely

## Port-list

Every public surface in:
- `knot/ast/types.py`, `knot/ast/expr.py`, `knot/ast/select.py`
- `knot/spec.py` (every public class)
- `knot/compile/*.py` (every emitter)
- The public re-exports from `knot/__init__.py`

## Coordination

- Your agent owns ONE module. Don't touch others.
- If you need a type from another module, **import by expected
  name** (matches the Python class name 1:1). The compile pass at
  the end resolves all references. Code that doesn't compile yet
  because another module isn't ported is fine — note it.
- Match Python field names verbatim where reasonable
  (`class_name` → `className`, `target_class_name` → `targetClassName`).
- One Java file per top-level type. Records nested only when they
  are explicitly a sub-type of a sealed interface defined in the
  same file.

## Zero-dep posture

`build.gradle.kts` drops jOOQ + Calcite + Jackson + Flyway from
main scope. Python knot ships zero runtime deps; Java keeps the
same posture. Test deps only.
