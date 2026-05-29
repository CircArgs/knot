# Keeping API DTOs, JPA Entities, and the DB Model in Sync

The recurring problem: every entity has three shapes — knot spec slots, JPA entity columns, and REST DTO fields — and hand-maintaining all three causes drift.

This doc covers the API ↔ JPA boundary specifically (the spec ↔ JPA boundary is a separate problem, addressed via knot's DDL emission).

---

## The Problem

| Shape | Source of truth for | Owned by |
|---|---|---|
| Knot spec slot | Domain model | Ontology / data team |
| JPA entity | Storage shape | Service team |
| REST DTO (record) | API contract | API team |

The three are *legitimately different* — collapsing them into one is wrong. The spec describes what an entity *is*. The JPA entity describes how it's *stored* (with ORM-specific concerns like fetch strategies). The DTO describes what's *exposed* (may be a subset, may add computed fields, may rename for clients).

What goes wrong with manual sync:
- DTO has a field the entity doesn't (caller sees null forever)
- Entity has a validation the DTO doesn't (4xx surface as 5xx)
- Slot is renamed in the spec and the DTO still uses the old name
- Adding a slot means editing three files in three syntaxes

---

## Recommended Pattern: MapStruct for Writes, JPA Projections for Reads

### Writes (request DTO → JPA entity)

Use **MapStruct** — annotation-based, generates conversion code at compile time. Type-checked, no reflection, the standard Java answer.

```java
public record MovieRequest(
    @NotBlank String title,
    @Min(1888) int year,
    String director,
    @NotEmpty List<String> genres
) {}

@Entity
@Table(name = "movies")
public class Movie {
    @Id private String canonicalId;
    private String title;
    private int year;
    private String director;
    @ElementCollection private List<String> genres;
    // constructors, getters
}

@Mapper(componentModel = "spring")
public interface MovieMapper {
    @Mapping(target = "canonicalId", ignore = true)
    Movie toEntity(MovieRequest request);
}
```

The mapper is one annotation. New slot? Add it to both the entity and the request record; MapStruct picks it up automatically as long as the field names match.

**When names don't match**, you annotate the mismatch: `@Mapping(source = "movieTitle", target = "title")`.

**When the shape diverges** (computed fields, conversions), you write a default method on the mapper. The compile-time generation still type-checks the rest.

### Reads (JPA entity → response DTO)

Use **JPA constructor projections** — the repository returns the DTO directly. No intermediate object, no mapping step.

```java
public record MovieResponse(
    String id,
    String title,
    int year,
    String director,
    Instant createdAt
) {}

public interface MovieRepository extends JpaRepository<Movie, String> {
    @Query("""
        SELECT new com.example.api.MovieResponse(
            m.canonicalId, m.title, m.year, m.director, m.createdAt
        )
        FROM Movie m
        WHERE m.year >= :since
    """)
    List<MovieResponse> findRecentResponses(@Param("since") int since);
}
```

The projection runs in SQL — only the columns named in the constructor expression are pulled from the database. Cheaper than `SELECT *` followed by mapping, and the response shape is enforced by the constructor signature.

### Why This Split

- Writes have a different shape problem: the request comes in as untrusted input, needs validation annotations (`@NotBlank`, `@Min`), and gets persisted. MapStruct sits exactly at the validation → persistence boundary.
- Reads have a different problem: efficiency. Loading the full entity to map a subset is wasteful. JPA projections push the shape down to SQL.
- Mixing the patterns (MapStruct for reads too, or projections for writes) works but loses the optimization story.

---

## Alternative Options Considered

### Option: Expose JPA Entities Directly as API

Skip DTOs entirely. The API returns the entity, the request binds to the entity.

**Pros**: zero mapping code, one shape per concept.
**Cons**: leaks ORM internals (lazy-loaded relations, `@JsonIgnore` everywhere), couples the API contract to the DB schema, security risk (mass assignment), versioning becomes impossible.

Verdict: only acceptable for internal admin APIs or prototypes.

### Option: Codegen DTOs From JPA Entities

Annotation processor reads `@Entity` classes, generates corresponding `Request`/`Response` records.

**Pros**: zero hand-maintenance.
**Cons**: brittle (entity changes cascade into generated code that breaks client compilation), the entity and DTO shapes are usually legitimately different (subset, renames, computed fields), tooling exists but isn't widely adopted.

Verdict: more friction than MapStruct, no real win.

### Option: Codegen Everything From the Spec

Knot spec generates JPA entities AND DTOs.

**Pros**: single source of truth.
**Cons**: forces the spec to model storage concerns (fetch strategies, indexes) and API concerns (validation messages, response field ordering). Spec stays clean by NOT modeling these.

Verdict: viable for greenfield services. Existing services with hand-written entities or DTOs are too costly to migrate. Probably the right direction for new code; not a forcing function for old code.

---

## Cross-Cutting Recommendations

1. **Constraints belong in two places, on purpose.** Validation annotations on the DTO (`@NotBlank`, `@Min`) reject bad input *before* it hits the entity. Constraint columns on the entity (`@Column(nullable=false)`) enforce at the DB level as a backstop. Yes, duplication. Yes, deliberate.

2. **Don't trust DB errors as the validation layer.** A `DataIntegrityViolationException` carries vendor-specific messages that can't be cleanly mapped to 4xx responses. Validate up front, let the DB constraint be the last line of defense, not the user-facing one.

3. **Bean Validation can read SOME constraints from JPA columns.** `@Column(length=255)` *can* be auto-translated to `@Size(max=255)` on a DTO via reflection, but the tooling is brittle and only covers a subset (length, nullability). Don't rely on it; write explicit DTO annotations.

4. **Profile MapStruct generated code.** It's straight bytecode, no reflection — should be on par with hand-written mapping. If it's slow, you've probably annotated something wrong (e.g., expensive expression in a `@Mapping`).

5. **One mapper interface per aggregate**, not per request/response pair. `MovieMapper` handles `MovieRequest → Movie` and `Movie → MovieResponse` and any other Movie-shape conversions. Keeps related conversions co-located.

---

## When to Revisit

If we ever migrate to a model where the knot spec emits both JPA entities AND OpenAPI specs (which can then generate DTOs via openapi-generator), we should re-evaluate. That's the spec-as-source-of-truth endgame. For now, MapStruct + JPA projections is the pragmatic answer.
