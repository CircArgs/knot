# Code style preferences (CircArgs)

**Status:** internal-use meta doc. Captures Nick's Python implementation taste, distilled from `ideal-spoon`, `nick-pimsleur`, `mini-vi`, `secret_project_cipher`, `practice`. Source repos are inspected for taste, not for transplant — knot has different design goals.

Apply this when writing knot LLD / implementation code. **General taste transfers; specific patterns may not** — flagged inline where relevant.

---

## Type hints

- **`from __future__ import annotations`** at the top of modules — standard in `secret_project_cipher`.
- **Modern union syntax preferred** for new code: `str | None`, `list[T]`, `dict[K, V]`, `tuple[T, ...]`.
- **Old-style `typing` imports tolerated** in `secret_project_cipher` (mixed-era `Dict`, `Optional`, `Union`, `List`); for knot, prefer uniformly modern.
- **Type aliases at module scope**: `LabelValue = str | list[str]` (seen in similar shape in `secret_project_cipher`).
- **TYPE_CHECKING blocks** for forward refs and import-cycle avoidance — used heavily in `secret_project_cipher`.
- **Type-parameterized Pydantic models** via `Generic[T]` — `VarRef[T]` in `secret_project_cipher/blocks/core.py`.
- **`Self` compatibility import** with try/except fallback to `typing_extensions`:
  ```python
  try:
      from typing import Self
  except ImportError:
      from typing_extensions import Self
  ```

---

## Pydantic patterns

Knot is Pydantic-heavy by design. Anchor to `secret_project_cipher` (the only Pydantic-substantive surveyed project):

- **`ConfigDict` explicitly**: `model_config = ConfigDict(arbitrary_types_allowed=True, extra='forbid')`. Strict on metaschema; allow arbitrary types where Pydantic can't validate everything.
- **`Field(default, description=...)`** — descriptions populated. `Field(None, description="The unique identity of this block.")`.
- **`Field(default_factory=...)`** for mutable defaults.
- **`SkipJsonSchema[...]`** for fields that exist on the model but shouldn't appear in JSON Schema (e.g., internal IDs, UI hints).
- **`ClassVar[...]`** for class-level config / metadata that isn't a field. Pattern: `studio_config: ClassVar[BlockStudioConfig] = BlockStudioConfig(...)`.
- **`@model_validator(mode='before')` with `@classmethod`** for input-shape transforms (e.g., normalizing strings → typed wrappers; auto-converting nested structures).
- **Section dividers** via `# --- N. Title ---` comments when a Pydantic-heavy module has logical sub-sections (e.g., variable refs / config / base class).
- **ABC base** via `class Block(BaseModel, ABC)` when an abstract Pydantic model has methods subclasses must implement. Used in `secret_project_cipher/blocks/block.py`.

---

## Dataclasses vs Pydantic

Both are seen across the surveyed projects. Pick the right tool:

- **Pydantic** — external interfaces, data needing validation, JSON serialization, field metadata, discriminated unions. The whole `secret_project_cipher/blocks/` framework is Pydantic-built.
- **Dataclasses (`@dataclass`)** — internal value objects, simple records with no validation. Pattern from `nick-pimsleur/srs_engine.py`:
  ```python
  @dataclass
  class ReviewEntry:
      """A single review event: an item reviewed in a specific lesson."""
      item_id: str                  # e.g. "v00001" or "g00001"
      item_type: str                # "vocab" or "grammar"
      ...
      def to_dict(self):
          d = asdict(self)
          d["review_mode"] = self.review_mode.value
          return d
  ```
- **`field(default_factory=...)`** for mutable defaults in dataclasses.
- **Custom `to_dict` method** when serialization needs slight massaging (e.g., enum → string).

The split is intentional: Pydantic for validated/external; dataclass for internal records that don't need it.

---

## Enums

- **`class X(str, Enum)`** for string-valued enums — JSON-serializable and human-readable. From `nick-pimsleur/srs_engine.py`:
  ```python
  class ReviewMode(str, Enum):
      EXPLICIT = "explicit"       # Cue → pause → learner produces → confirmation (4s/item)
      CONTEXTUAL = "contextual"   # Item embedded in scenario/dialogue (2s/item)
      PASSIVE = "passive"         # Item appears naturally in speech (0.5s/item)
  ```
- **Inline comments per value** when the value carries domain nuance.
- **Plain `Enum`** when values are arbitrary integers / not stringly-meaningful.

---

## Constants and lookup tables

Module-level uppercase constants for fixed schedules / lookups. Pattern from `nick-pimsleur/db_loader.py` and `srs_engine.py`:

```python
LEVEL_ORDER = ["N5", "N4", "N3", "N2", "N1"]
LEVEL_LESSON_COUNTS = {"N5": 60, "N4": 70, "N3": 90, "N2": 90, "N1": 110}
GLOBAL_OFFSETS = {"N5": 0, "N4": 60, "N3": 130, "N2": 220, "N1": 310}

# Cumulative offset comments inline; complex tables prefer named struct/tuple
CROSS_LESSON_SCHEDULE = [
    (1,  ReviewMode.EXPLICIT),    # +1 lesson
    (3,  ReviewMode.EXPLICIT),    # +3 lessons
    ...
]
```

- Aligned-comment annotations for tabular data.
- Plain `dict` / `list[tuple]` rather than dataclasses for static lookup tables.

---

## Functions, helpers, modules

- **Module-level functions for stateless ops.** Don't wrap in classes unless state needs encapsulating.
- **Private helpers prefixed `_`** — `_extract_list`, `_normalize_label` style. Used in `nick-pimsleur/db_loader.py`.
- **Action-oriented function docstrings** — short, often single-line. Pattern: "Load the merged vocab DB for a level. Returns a list of vocab item dicts, each with at least 'id' and 'lesson' fields."
- **Module docstrings**: header-style multi-line when the module's purpose / context warrants explanation. Pattern from `nick-pimsleur/srs_engine.py`:
  ```python
  """
  SRS Engine — Static Review Schedule Generator for Pimsleur-Gen

  Computes the pre-baked review schedule for every vocabulary and grammar item
  across all lessons. The output is a review matrix: ...

  This is NOT an adaptive algorithm. It produces a fixed schedule at authoring time.
  Every learner gets the same reviews in the same lessons.
  """
  ```
  Otherwise short single-line description.

---

## Errors

- **Raise with informative messages**, often via f-strings: `raise FileNotFoundError(f"Vocab DB not found: {path}")`.
- **Custom exception classes** when the domain warrants typed errors (use plain inheritance; bare class).
- **Errors raise; never silently swallow.** Matches Nick's design-thinking pattern.

---

## Paths and I/O

- **`pathlib.Path` everywhere** — never raw strings for filesystem paths.
- **`PROJECT_ROOT` pattern** for derived paths:
  ```python
  PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
  VOCAB_DIR = PROJECT_ROOT / "03-databases" / "vocabulary"
  ```
- **Explicit `encoding="utf-8"`** on file open: `with open(path, encoding="utf-8") as f`.
- **`Path.glob()`** for pattern-based file collection.
- **Defensive parsing** of varied input shapes:
  ```python
  def _extract_list(raw: dict | list, key: str) -> list[dict]:
      """Extract item list from DB file (handles both plain arrays and {metadata, key} wrappers)."""
      if isinstance(raw, list):
          return raw
      return raw.get(key, raw.get("items", []))
  ```

---

## Testing

- **pytest** as default. `pytest-asyncio` for async (seen in `secret_project_cipher` dev deps).
- **Plain assertions**, no special assertion libraries.
- **`pytest.raises(ExpectedError)`** for expected failures.
- **Tests live at top-level `tests/`**, not nested inside the package.

(Less testing material in the surveyed projects; reanchor when more taste signal surfaces.)

---

## Build / tooling

- **`requires-python = ">=3.10"`** is the floor seen in `secret_project_cipher`. New knot work should aim higher (3.11+) for type-syntax improvements.
- **`setuptools` build backend** seen in `secret_project_cipher`. Other build backends (hatchling, etc.) are fine; pick one and stick.
- **Dependencies are minimal**, declared explicitly with version floors. No kitchen-sink imports.
- **`[project.optional-dependencies] dev`** for test/lint deps.
- **Type-checker preference**: not strongly signaled in surveyed projects — left open.
- **Linter preference**: not strongly signaled — left open.

---

## Strings, formatting, comments

- **f-strings** for formatted messages and debug output.
- **Mixed quote styles** within a file are tolerated — consistency within a block matters more than per-project rules.
- **Inline comments next to fields / values** when domain context matters; not redundant with type annotations.
- **Section divider comments** (`# --- N. Title ---`) for multi-section modules.

---

## What NOT to transplant

- **OpenTelemetry boilerplate** seen in `secret_project_cipher/pyproject.toml`. Telemetry is its own decision; don't pre-wire.
- **Studio / UI metadata classes** (`BlockStudioConfig`, etc. in `secret_project_cipher`) — that's framework UI scaffolding for a different system.
- **Mixed-era typing** in `secret_project_cipher` — uniformly modern hints in knot.
- **Heavy `Block(BaseModel, ABC)` patterns** when a `Protocol` would do — knot's design favors typed protocols + DataContexts over deep ABC hierarchies.
- **`secret_project_cipher`'s `VarRef[T]` runtime resolution machinery** — that's a reactive-template system specific to that framework.
- **Practice-problem code patterns** (`ideal-spoon`, `practice`) — those are interview-shaped scratch code, not project-shape.
- **`mini-vi`'s monolithic single-file pattern** — fine for a 100-line utility; not the shape for knot.

---

## Pilot test — recommended next step

To validate the style capture, draft one small representative module from knot's design. Recommended pilot: **`knot/spec/expression.py`** — the expression tree node types from `derivation-and-constraints.md`. Self-contained, ~100-150 lines, exercises:

- Pydantic class hierarchy + discriminated union
- Real-refs principle (`SlotPath` references `Slot` and `OntologyClass` directly)
- Modern type hints throughout
- Enums (`CompareOp`, `BoolOpKind`, `AggFunc`, `GroupByMode`)
- Module organization + imports + section dividers
- Docstring style

If the pilot feels right, the style capture is working. If it doesn't, refine this doc and try again.

---

## Maintenance

Update this doc when new style preferences surface (either from new repos or from real-time corrections during knot implementation). Distinguish:

- **General taste** — applies broadly, transfers to knot.
- **Project-specific patterns** — flagged in "What NOT to transplant" so they don't leak in.
