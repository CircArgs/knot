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

## Build + test (containerized — no host JDK required)

Everything runs through `docker compose`. Two services: `postgres`
(the data plane on host port 5433) and `gradle` (JDK 21 + Gradle 8.7
with the repo mounted at `/workspace`).

```bash
docker compose up -d                              # bring both up
docker compose run --rm gradle gradle test        # run tests
docker compose run --rm gradle gradle wrapper     # generate gradlew + jar (one-off)
docker compose exec gradle sh                     # open a dev shell
docker compose exec postgres psql -U knot -d knot # psql against the data plane
docker compose down                               # stop; keep volumes
docker compose down -v                            # stop AND wipe state
```

Connection defaults:

| From | JDBC URL |
| - | - |
| host (IDE, ad-hoc) | `jdbc:postgresql://localhost:5433/knot` |
| `gradle` service   | `jdbc:postgresql://postgres:5432/knot` (compose network) |

User / password / database are all `knot`. Integration tests read
`KNOT_PG_URL` / `KNOT_PG_USER` / `KNOT_PG_PASS` from the environment;
the `gradle` service pre-sets them to the in-network URL above.

### Running on a host with JDK installed

If you have OpenJDK 21 + Gradle 8 installed locally, you can skip the
`gradle` service and run `./gradlew test` / `./gradlew build` against
the postgres container at `localhost:5433`. Generate the wrapper first
with a system-installed Gradle:

```bash
gradle wrapper --gradle-version 8.7
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
