# Spec versioning + canonical serialization

**Status:** staging — captured for review, not yet integrated into authoritative docs.

knot's spec is content-addressed via the compile-hash machinery in `compiled-workflow-hashing.md`. The hash depends on the canonical serialized form of the Pydantic spec. **Canonicalization is part of knot's spec layer** — knot computes the canonical bytes, hashes them, stores in `compiled_workflows.hash`.

---

## What canonicalization means

For two semantically-equivalent spec revisions to hash identically, knot must:

- **Sort dict keys consistently** — alphabetical at every nesting level.
- **Strip default values** — empty containers and explicit field defaults removed before hashing.
- **Normalize numeric representations** — no `1.0` vs `1` drift.
- **Pin the Pydantic serialization mode explicitly** — `model_dump_json()` defaults can shift between Pydantic minor versions; knot builds a Pydantic-independent intermediate instead.
- **Define what fields are CANONICAL vs RUNTIME** — RUNTIME fields are excluded from the hashed bytes.

Without stable canonicalization, every Pydantic minor-version bump risks rehashing every existing compiled spec — invalidating audit walk-back from prior runs.

---

## Strip defaults

Before hashing, remove:

- Empty containers (`[]`, `{}`, `set()`) that are field defaults.
- Fields set to their explicit `default=` or `default_factory=` value.

Rationale: adding a new optional field with a default must not rehash existing specs. Standard convention (dbt manifests, JSON Schema `additionalProperties`).

---

## Pydantic Config policy

One shared base class; all metaschema classes inherit. No exceptions.

```python
from pydantic import BaseModel, ConfigDict

class SpecBase(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        ser_json_bytes="base64",
        ser_json_inf_nan="strings",
        validate_assignment=True,
        frozen=False,
        use_enum_values=True,
        str_strip_whitespace=False,
    )
```

**Aliases forbidden** on all metaschema classes. `populate_by_name=True` is set defensively. `extra="forbid"` on every class — unknown fields raise `ValidationError` at parse time, preventing silent spec drift.

---

## Hash format

`sha256` hex string, 64 characters. Stored in `compiled_workflows.hash CHAR(64)`.

Algorithm lock-in on sha256 is intentional. Future-flexibility (BLAKE3, multihash, prefix tags like `sha256:<hex>`) would require widening the column; a forced flag-day is a real cost we don't take on speculatively.

---

## Canonical JSON dialect

RFC 8785 (JSON Canonicalization Scheme, JCS). JCS specifies:

- UTF-8 encoding.
- No insignificant whitespace.
- Object keys sorted lexicographically by Unicode code point.
- Numeric serialization per ECMAScript `ToString` (eliminates `1.0` vs `1` drift).

Use a JCS library (e.g., `jcs` on PyPI) rather than `json.dumps(sort_keys=True)`. `json.dumps` numeric serialization is not JCS-compliant.

---

## Canonicalizer versioning

Module-level constant:

```python
CANONICAL_DUMP_VERSION: int = 1
```

This is **not a field on `Spec`** — embedding it would be circular (changing the version would change the spec, which would change the hash). It is stored externally on `compiled_workflows.canonicalizer_version SMALLINT NOT NULL DEFAULT 1`.

Without this column, knot cannot detect which rows were hashed under which algorithm and cannot trigger selective recompile when the canonicalizer changes. Adding the column is cheap; adding it later is a flag-day.

Increment `CANONICAL_DUMP_VERSION` only when the canonicalization algorithm changes in a way that would produce different bytes for the same logical spec. Incrementing triggers a recompile of affected compiled specs (knot's runtime concern).

---

## Pydantic v3 migration

`canonical_dump` builds a **Pydantic-independent intermediate** (plain `dict` / `list` / Python scalars) before JCS encoding. This is the isolation layer.

Migration protocol when Pydantic v3 lands:

1. Build a differential test corpus: representative specs → `canonical_dump` bytes under v2.
2. Install v3 in a separate venv; run corpus against the same `canonical_dump` path.
3. If any byte differs: freeze the v2 path as `_canonical_dump_v1`; implement a v3-native path; bump `CANONICAL_DUMP_VERSION` to 2.
4. On version bump: knot triggers recompile-on-mismatch (rows where `canonicalizer_version != CANONICAL_DUMP_VERSION`). No eager rehash of all rows — recompile happens on next trigger for each target.

If no bytes differ under v3, no version bump and no migration needed.

---

## RUNTIME vs CANONICAL field taxonomy

Default: **CANONICAL**. Fields are included in the hashed bytes unless explicitly listed as RUNTIME below.

**RUNTIME (excluded from hash):**

| Field | Class | Note |
|---|---|---|
| `description` | `OntologyClass`, `Slot`, `SlotOverride`, `PermissibleValue`, `TypeDefinition`, `Constraint` | Typo fixes shouldn't rehash the universe; matches dbt manifest convention. |
| `created_at` | Spec envelope | Authoring metadata |
| `last_modified` | Spec envelope | Authoring metadata |
| `author` | Spec envelope | Authoring metadata |
| `revision_id` | Spec envelope | Authoring metadata |
| `display_label` | Spec envelope | Authoring metadata |

**Never hashed (derived artifacts):**

`SpecGraph`, `Change`, `Reference` — computed from the spec; not inputs to it.

---

## API

```python
CANONICAL_DUMP_VERSION: int = 1

def canonical_dump(spec: Spec) -> bytes:
    """Pydantic-independent intermediate -> strip defaults -> RUNTIME fields removed -> JCS encode."""
    ...

def compute_content_hash(spec: Spec) -> str:
    """sha256(canonical_dump(spec)).hexdigest() — bare 64-char hex."""
    return sha256(canonical_dump(spec)).hexdigest()
```

`canonical_dump` steps:

1. `spec.model_dump(mode="python", exclude_unset=False)` to get the full dict.
2. Recursively strip RUNTIME fields by field path.
3. Recursively strip defaults.
4. JCS encode (RFC 8785).
5. Return bytes.

---

## Test coverage

- **Round-trip:** `model → canonical_dump → parse → model'` produces a structurally-equivalent model.
- **Stability:** same model dumped twice produces identical bytes.
- **Determinism:** equivalent models with different field-order or default-explicitness produce the same hash.
- **Strip-defaults:** model with explicit defaults set to their default value hashes identically to model without those fields set.
- **RUNTIME exclusion:** mutating a RUNTIME field (e.g., `description`) does not change `compute_content_hash`.
- **Pydantic version-bump corpus:** representative specs serialized under the current Pydantic minor; output stored as golden bytes; CI asserts no drift on minor-version upgrades.
