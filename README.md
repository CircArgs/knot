# knot

Reflective ontology compiler — Java port. A thin spec → SQL emitter
that layers multi-source trust-resolved bindings on top of a host
substrate (jOOQ for tables / FKs / queries, Flyway for migrations).

## Posture

knot owns only what is genuinely unique to the reflective-ontology /
multi-source-claim domain:

- A `Spec` declared via records + sealed interfaces.
- SCD2 binding tables per concrete class.
- A `<class>_resolved` view that argmaxes per-slot across currently-
  open bindings via each source's `accuracy`-derived Beta posterior.
- Spec-aware AST rewriting of constraint bodies and binding mappings
  (via Apache Calcite).
- `_user_corrections` synthetic source layered into the same trust
  resolution.
- Spec persistence into `knot_meta.*` (round-trips a `Spec` through
  postgres).

Everything else — entity-table DDL, FK constraints, indexes, batch
INSERT performance, schema migrations, async / connection pooling — is
delegated to jOOQ + Flyway.

## Build

Requires JDK 21.

```bash
./gradlew build
```

Generate the wrapper for the first time (one-off, run with a
system-installed Gradle 8.x):

```bash
gradle wrapper --gradle-version 8.7
```

## Test

```bash
./gradlew test
```

## Layout

```
src/main/java/knot/
  spec/         metaschema records + sealed interfaces
  compile/      pure spec → SQL emitters
src/test/java/knot/
                JUnit 5 test suites
```

## Cross-language note

The Python prototype on the `library/v0` branch is the reference for
this port. Golden tests (planned) verify byte-identical output between
the two tracks for the same input spec.
