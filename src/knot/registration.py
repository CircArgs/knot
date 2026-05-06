"""DataContext validation at impl registration time.

Per core-design.md commitments 4 and 16:
- Every spec entity referenced by a DataContext is statically traceable.
- Broken refs fail registration with loud errors (class + slot + impl context).
"""

from __future__ import annotations

from knot.metaschema import OntologyClass, Slot, Spec
from knot.protocols import DataContext
from knot.impact import datacontext_refs


class DataContextValidationError(Exception):
    """Raised when a DataContext references a spec entity that doesn't exist."""

    def __init__(
        self,
        impl_name: str,
        datacontext_attr: str,
        ref_type: str,
        ref_name: str,
        did_you_mean: str | None = None,
    ) -> None:
        self.impl_name = impl_name
        self.datacontext_attr = datacontext_attr
        self.ref_type = ref_type
        self.ref_name = ref_name
        self.did_you_mean = did_you_mean
        hint = f" (did you mean: {did_you_mean!r}?)" if did_you_mean else ""
        super().__init__(
            f"[{impl_name}.{datacontext_attr}] references unknown {ref_type} "
            f"{ref_name!r}{hint}"
        )


def validate_datacontexts(impl_class: type, spec: Spec) -> None:
    """Walk each DataContext attribute on impl_class; raise if any ref is missing.

    Per commitment 16: parse-time errors with field path + did-you-mean.
    Per commitment 4: every spec entity referenced by a DataContext is
    statically traceable.
    """
    impl_name = impl_class.__name__
    known_classes = {c.name for c in spec.classes}
    known_slots = {s.name for s in spec.slots}

    for attr_name in dir(impl_class):
        try:
            val = getattr(impl_class, attr_name)
        except Exception:
            continue
        if not isinstance(val, DataContext):
            continue
        for ref in datacontext_refs(val):
            if isinstance(ref, OntologyClass) and ref.name != "__sentinel__":
                if ref.name not in known_classes:
                    raise DataContextValidationError(
                        impl_name, attr_name, "OntologyClass", ref.name,
                        _closest(ref.name, known_classes),
                    )
            elif isinstance(ref, Slot):
                if ref.name not in known_slots:
                    raise DataContextValidationError(
                        impl_name, attr_name, "Slot", ref.name,
                        _closest(ref.name, known_slots),
                    )


def _closest(name: str, candidates: set[str]) -> str | None:
    """Return the candidate closest to name by edit distance, or None."""
    if not candidates:
        return None
    name_l = name.lower()
    best, best_score = None, len(name) + 1
    for c in candidates:
        d = _levenshtein(name_l, c.lower())
        if d < best_score:
            best_score, best = d, c
    return best if best is not None and best_score <= max(2, len(name) // 2) else None


def _levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        curr = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            curr[j] = min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + (ca != cb))
        prev = curr
    return prev[len(b)]
