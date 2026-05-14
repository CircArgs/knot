"""Flyway file renderer — turn a ``list[MigrationOp]`` into the
``V<version>__<slug>.sql`` / ``R__<slug>.sql`` files Flyway picks up.

Split policy:

  - **V file** (versioned, runs once per version): structural changes
    only — schema, tables, indexes, FKs, column alters, drops, renames.
    These are not safe to re-run, so they need a versioned filename
    and Flyway's schema-history table tracks "applied".
  - **R files** (repeatable, re-runs whenever checksum changes): the
    idempotent reconciliation ops — trust seed (``INSERT … ON CONFLICT
    DO UPDATE``), resolved views (``CREATE OR REPLACE VIEW``), virtual
    class views. Two R files for ordering: ``R__001_trust_seed.sql``
    runs first so the views' ``LEFT JOIN source_accuracy`` sees the
    current rows.

Usage::

    ops = diff_against_db(spec, conn, allow_destructive=True)
    files = emit_flyway_files(
        ops,
        version="20260514_001",
        slug="add_runtime_minutes",
    )
    for filename, body in files.items():
        (migrations_dir / filename).write_text(body)

Returns ``{}`` if no ops were generated (no-op diff).
"""

from __future__ import annotations

from collections.abc import Iterable

from knot.compile.migrate import MigrationOp


_STRUCTURAL_TARGETS: frozenset[str] = frozenset(
    {"schema", "trust_table", "canonical", "bindings", "index", "fk"}
)
_TRUST_SEED_TARGETS: frozenset[str] = frozenset({"trust_seed"})
_VIEW_TARGETS: frozenset[str] = frozenset({"resolved_view", "virtual_view"})


def emit_flyway_files(
    ops: Iterable[MigrationOp],
    *,
    version: str,
    slug: str = "schema_change",
) -> dict[str, str]:
    """Return ``{filename: body}`` ready to write to a Flyway directory.

    ``version`` is the numeric / underscore-delimited Flyway version
    (e.g. ``"20260514_001"``). ``slug`` is the human-readable
    description of the change; underscores and lowercase only by
    convention.

    Structural ops land in ``V<version>__<slug>.sql``. Idempotent ops
    (trust seed, views) land in the repeatable files
    ``R__001_trust_seed.sql`` and ``R__002_resolved_views.sql``, which
    Flyway re-runs whenever their checksum changes.

    Empty groups produce no file — a no-op diff returns ``{}``.
    """
    ops_list = list(ops)
    structural = [op for op in ops_list if op.target in _STRUCTURAL_TARGETS]
    trust_seed = [op for op in ops_list if op.target in _TRUST_SEED_TARGETS]
    views = [op for op in ops_list if op.target in _VIEW_TARGETS]

    files: dict[str, str] = {}
    if structural:
        files[f"V{version}__{slug}.sql"] = _render(
            structural,
            title=f"V{version}__{slug}",
            note=(
                "Versioned migration — runs once per version. Flyway "
                "tracks applied state in flyway_schema_history."
            ),
        )
    if trust_seed:
        files["R__001_trust_seed.sql"] = _render(
            trust_seed,
            title="R__001_trust_seed",
            note=(
                "Repeatable — Flyway re-runs when the file's checksum "
                "changes. Reconciles knot_data.source_accuracy with "
                "the spec's SourceBinding accuracies. Idempotent "
                "(INSERT … ON CONFLICT DO UPDATE)."
            ),
        )
    if views:
        files["R__002_resolved_views.sql"] = _render(
            views,
            title="R__002_resolved_views",
            note=(
                "Repeatable — CREATE OR REPLACE VIEW for every resolved "
                "view + virtual class. Runs after R__001_trust_seed so "
                "the views' LEFT JOIN sees the current accuracy rows."
            ),
        )
    return files


def _render(ops: list[MigrationOp], *, title: str, note: str) -> str:
    """Render a list of ops as a single SQL file body. Header comments
    summarize what's in the file; each op gets a one-line description
    comment before its SQL."""
    header = _wrap_comment([title, "", note, "", _summary_line(ops)])
    blocks: list[str] = [header]
    for op in ops:
        destructive_tag = "  [DESTRUCTIVE]" if op.destructive else ""
        blocks.append(f"-- {op.description}{destructive_tag}")
        blocks.append(op.sql)
        blocks.append("")  # blank line between ops
    return "\n".join(blocks).rstrip() + "\n"


def _wrap_comment(lines: list[str]) -> str:
    """Render multi-line header as SQL line comments."""
    return "\n".join(f"-- {line}" if line else "--" for line in lines)


def _summary_line(ops: list[MigrationOp]) -> str:
    """``3 ops: 1 create_table, 2 add_column``"""
    if not ops:
        return "0 ops"
    counts: dict[str, int] = {}
    for op in ops:
        # First underscore-prefix is the action verb (create_table,
        # add_column, drop_fk, alter_column_type, …).
        verb = "_".join(op.description.split("_")[:2])
        counts[verb] = counts.get(verb, 0) + 1
    parts = sorted(f"{n} {v}" for v, n in counts.items())
    return f"{len(ops)} ops: {', '.join(parts)}"


__all__ = ["emit_flyway_files"]
