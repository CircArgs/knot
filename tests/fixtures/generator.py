"""Synthetic fixture generator for knot test tiers.

Each tier is fully deterministic: re-running with the same TierConfig
produces byte-identical output. All randomness is derived from tier.seed.
"""

from __future__ import annotations

import csv
import io
import random
import textwrap
from pathlib import Path
from typing import Any

from tests.fixtures.tier_configs import TierConfig


# ---------------------------------------------------------------------------
# Row-level edge-case seeders (category id → mutation applied to a row dict)
# ---------------------------------------------------------------------------

def _seed_cat_1_1(row: dict, rng: random.Random) -> dict:
    """1.1 — Null values in optional slots."""
    row = dict(row)
    if rng.random() < 0.5:
        row["runtime_minutes"] = ""
    else:
        row["rating"] = ""
    return row


def _seed_cat_1_2(row: dict, _rng: random.Random) -> dict:
    """1.2 — Null value in required slot (title)."""
    row = dict(row)
    row["title"] = ""
    return row


def _seed_cat_1_3(row: dict, rng: random.Random) -> dict:
    """1.3 — Empty string vs null disambiguation."""
    row = dict(row)
    # Empty string (not CSV-blank) in an optional slot — semantically distinct from absent
    if rng.random() < 0.5:
        row["rating"] = "0.0"   # present but zero — distinct from null
    else:
        row["runtime_minutes"] = "0"
    return row


def _seed_cat_1_4(row: dict, rng: random.Random) -> dict:
    """1.4 — Leading/trailing/internal multiple whitespace."""
    row = dict(row)
    kind = rng.choice(["leading", "trailing", "internal"])
    if kind == "leading":
        row["title"] = "  " + row["title"]
    elif kind == "trailing":
        row["title"] = row["title"] + "   "
    else:
        parts = row["title"].split()
        row["title"] = "  ".join(parts) if len(parts) > 1 else row["title"] + "  extra"
    return row


def _seed_cat_1_9(row: dict, rng: random.Random) -> dict:
    """1.9 — Numeric edge values (year=0, runtime=0)."""
    row = dict(row)
    if rng.random() < 0.5:
        row["year"] = "0"
    else:
        row["runtime_minutes"] = "0"
    return row


def _seed_cat_1_11(row: dict, rng: random.Random) -> dict:
    """1.11 — Trailing whitespace in CSV cells."""
    row = dict(row)
    field = rng.choice(["title", "genres"])
    row[field] = row[field] + "   "
    return row


def _seed_cat_1_13(row: dict, _rng: random.Random) -> dict:
    """1.13 — Missing identifier (blank imdb_id)."""
    row = dict(row)
    row["imdb_id"] = ""
    return row


_SEEDERS: dict[str, Any] = {
    "1.1": _seed_cat_1_1,
    "1.2": _seed_cat_1_2,
    "1.3": _seed_cat_1_3,
    "1.4": _seed_cat_1_4,
    "1.9": _seed_cat_1_9,
    "1.11": _seed_cat_1_11,
    "1.13": _seed_cat_1_13,
}

# ---------------------------------------------------------------------------
# Synthetic movie data pools (fixed; index addressed for determinism)
# ---------------------------------------------------------------------------

_TITLES = [
    "The Matrix", "The Sixth Sense", "Fight Club", "American Beauty",
    "The Green Mile", "Magnolia", "Eyes Wide Shut", "The Insider",
    "Being John Malkovich", "Three Kings", "Office Space", "Election",
    "Rushmore", "The Limey", "Go", "Run Lola Run", "Dark City",
    "Pleasantville", "Pi", "Rounders", "Lock Stock and Two Smoking Barrels",
    "Blade", "Dark City", "Buffalo 66", "The Truman Show",
    "There's Something About Mary", "Saving Private Ryan", "The Big Lebowski",
    "Happiness", "Velvet Goldmine", "A Simple Plan", "The Negotiator",
    "Armageddon", "Snake Eyes", "Meet Joe Black", "Patch Adams",
    "The Waterboy", "You've Got Mail", "Ronin", "Enemy of the State",
    "Deep Impact", "City of Angels", "Sphere", "Lethal Weapon 4",
    "The Mask of Zorro", "Blade Runner", "Gattaca", "Contact",
    "L.A. Confidential", "Boogie Nights",
]

_GENRES_POOL = [
    "Action", "Adventure", "Comedy", "Crime", "Drama",
    "Fantasy", "Horror", "Mystery", "Romance", "Sci-Fi",
    "Thriller", "Western", "Animation", "Documentary",
]

_BASE_YEAR = 1990
_YEARS = list(range(1990, 2001))


def _make_base_rows(n: int, rng: random.Random) -> list[dict]:
    """Generate n baseline Movie rows, all fields populated."""
    rows = []
    for i in range(n):
        title_idx = i % len(_TITLES)
        imdb_id = f"tt{1000000 + i:07d}"
        title = _TITLES[title_idx]
        year = rng.choice(_YEARS)
        runtime = rng.randint(85, 200)
        rating = round(rng.uniform(5.0, 9.5), 1)
        g1 = rng.choice(_GENRES_POOL)
        g2 = rng.choice([g for g in _GENRES_POOL if g != g1])
        genres = f"{g1}|{g2}" if rng.random() > 0.3 else g1
        rows.append({
            "imdb_id": imdb_id,
            "title": title,
            "year": str(year),
            "runtime_minutes": str(runtime),
            "rating": str(rating),
            "genres": genres,
        })
    return rows


# ---------------------------------------------------------------------------
# Edge-case seeding plan for A1
# ---------------------------------------------------------------------------

# (category_key, target_count, row_indices_offset)
# Row indices are chosen deterministically so cases are spread through the file.
_A1_EDGE_PLAN: list[tuple[str, int]] = [
    ("1.1",  5),   # null optionals
    ("1.2",  2),   # null required (title)
    ("1.3",  3),   # empty-string vs null
    ("1.4",  3),   # whitespace
    ("1.9",  2),   # numeric edges
    ("1.11", 5),   # trailing whitespace in cells
    ("1.13", 2),   # missing imdb_id
]


def _apply_edge_cases(rows: list[dict], rng: random.Random) -> tuple[list[dict], list[tuple[str, int]]]:
    """Seed edge cases into rows in a spread pattern. Returns mutated rows
    and a list of (category_key, row_index) pairs for the manifest."""
    rows = [dict(r) for r in rows]
    n = len(rows)
    manifest: list[tuple[str, int]] = []

    # Distribute target indices evenly across the row space; avoid pile-up at end.
    used: set[int] = set()
    for cat_key, count in _A1_EDGE_PLAN:
        seeder = _SEEDERS[cat_key]
        # Spread these `count` rows evenly across the file
        step = max(1, n // (count + 1))
        indices: list[int] = []
        for j in range(count):
            candidate = (j + 1) * step + rng.randint(0, step - 1)
            candidate = candidate % n
            # Avoid reusing an index for a destructive seeder (1.2, 1.13)
            if cat_key in ("1.2", "1.13"):
                while candidate in used:
                    candidate = (candidate + 1) % n
                used.add(candidate)
            indices.append(candidate)
        for idx in indices:
            rows[idx] = seeder(rows[idx], rng)
            manifest.append((cat_key, idx))

    return rows, manifest


# ---------------------------------------------------------------------------
# File writers
# ---------------------------------------------------------------------------

def _write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["imdb_id", "title", "year", "runtime_minutes", "rating", "genres"]
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    path.write_text(buf.getvalue(), encoding="utf-8")


def _canonical_id(imdb_id: str) -> str:
    return f"mov_imdb_{imdb_id}" if imdb_id else "mov_imdb_unknown"


def _write_expected_facts(path: Path, rows: list[dict]) -> None:
    """Write ground-truth facts for clean rows (non-edge-case rows with valid imdb_id + title)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    # Pick up to 10 rows that have valid imdb_id and title for ground-truth
    clean = [r for r in rows if r.get("imdb_id") and r.get("title") and r.get("title").strip()][:10]
    lines = ["movies:"]
    for row in clean:
        cid = _canonical_id(row["imdb_id"])
        title = row["title"].strip()
        year = row["year"]
        runtime = row["runtime_minutes"] if row["runtime_minutes"] else "null"
        rating = row["rating"] if row["rating"] else "null"
        raw_genres = row["genres"].strip()
        genres_list = [f'"{g.strip()}"' for g in raw_genres.split("|")]
        genres_yaml = "[" + ", ".join(genres_list) + "]"
        lines.append(f'  {cid}:')
        lines.append(f'    title: "{title}"')
        lines.append(f'    year: {year}')
        lines.append(f'    runtime_minutes: {runtime}')
        lines.append(f'    rating: {rating}')
        lines.append(f'    genres: {genres_yaml}')
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_edge_cases_manifest(
    path: Path, rows: list[dict], manifest: list[tuple[str, int]]
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Group by category key
    by_cat: dict[str, list[int]] = {}
    for cat_key, idx in manifest:
        by_cat.setdefault(cat_key, []).append(idx)

    descriptions = {
        "1.1": "Null values in optional slots",
        "1.2": "Null values in required slots → fail validation",
        "1.3": "Empty string vs null (semantically distinct)",
        "1.4": "Leading/trailing/internal multiple whitespace",
        "1.9": "Numeric edge values (year=0, runtime=0)",
        "1.11": "Trailing whitespace in CSV cells",
        "1.13": "Source rows with missing identifier → drop or fail per source spec",
    }

    # Ordered fields to inspect per category when detecting the seeded value
    cat_fields: dict[str, list[str]] = {
        "1.1":  ["runtime_minutes", "rating"],
        "1.2":  ["title"],
        "1.3":  ["rating", "runtime_minutes"],
        "1.4":  ["title"],
        "1.9":  ["year", "runtime_minutes"],
        "1.11": ["title", "genres"],
        "1.13": ["imdb_id"],
    }

    lines = []

    for cat_key in sorted(by_cat):
        desc = descriptions.get(cat_key, "")
        fields = cat_fields.get(cat_key, ["unknown"])
        lines.append(f'"{cat_key}":')
        lines.append(f'  description: "{desc}"')
        lines.append(f'  cases:')
        for idx in by_cat[cat_key]:
            row = rows[idx]
            imdb_id = row.get("imdb_id", "")
            cid = _canonical_id(imdb_id) if imdb_id else f"mov_imdb_row{idx:04d}"
            # Find the actual field that was mutated and report its seeded value
            fields = cat_fields.get(cat_key, ["unknown"])
            seeded_slot = fields[0]
            seeded_val = row.get(fields[0], "")
            for f in fields:
                v = row.get(f, "")
                # Blank field → was nulled
                if v == "":
                    seeded_slot = f
                    seeded_val = v
                    break
                # Whitespace seeded (1.4, 1.11)
                if cat_key in ("1.4", "1.11") and (v != v.strip() or "  " in v):
                    seeded_slot = f
                    seeded_val = v
                    break
                # Numeric zero (1.9)
                if cat_key == "1.9" and v == "0":
                    seeded_slot = f
                    seeded_val = v
                    break
            seeded_str = "null" if seeded_val == "" else f'"{seeded_val}"'
            expected = "validation_error" if cat_key == "1.2" else "drop" if cat_key == "1.13" else "normalized"
            lines.append(f'    - canonical_id: {cid}')
            lines.append(f'      row_index: {idx}')
            lines.append(f'      slot: {seeded_slot}')
            lines.append(f'      seeded_value: {seeded_str}')
            lines.append(f'      expected: {expected}')
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_spec(path: Path, _tier: TierConfig) -> None:
    """Write spec.py — this is a static file for A1; generator writes it verbatim."""
    # For A1 the spec is fixed content; we just ensure it's present.
    # The actual spec content lives in A1/spec.py (created separately).
    # Generator only writes it if it doesn't already exist, preserving hand-authored edits.
    if not path.exists():
        path.write_text(
            "# Auto-generated placeholder — replace with real A1/spec.py content\n",
            encoding="utf-8",
        )


def _write_impl_placeholder(path: Path) -> None:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "# Auto-generated placeholder — replace with real impl content\n",
            encoding="utf-8",
        )


# ---------------------------------------------------------------------------
# Main generator class
# ---------------------------------------------------------------------------

class FixtureGenerator:
    def __init__(self, tier: TierConfig) -> None:
        self.tier = tier
        self._rng = random.Random(tier.seed)

    def write(self, out_dir: Path) -> None:
        """Produce all fixture files for the tier under out_dir."""
        out_dir.mkdir(parents=True, exist_ok=True)

        for cls_name in self.tier.classes:
            sources = self.tier.sources_per_class.get(cls_name, [])
            volume = self.tier.base_volumes.get(cls_name, 50)

            for source_name in sources:
                # Fresh RNG per (class, source) using a derived seed for isolation
                src_seed = self.tier.seed ^ hash(f"{cls_name}:{source_name}") & 0xFFFFFFFF
                src_rng = random.Random(src_seed)

                rows = _make_base_rows(volume, src_rng)
                rows, manifest = _apply_edge_cases(rows, src_rng)

                csv_path = out_dir / "sources" / f"{source_name}.csv"
                _write_csv(csv_path, rows)

                facts_path = out_dir / "expected_facts.yaml"
                _write_expected_facts(facts_path, rows)

                ec_path = out_dir / "edge_cases.yaml"
                _write_edge_cases_manifest(ec_path, rows, manifest)

        # Write spec placeholder (preserves hand-authored spec.py)
        _write_spec(out_dir / "spec.py", self.tier)

        # Write impl placeholders
        _write_impl_placeholder(out_dir / "impls" / "iceberg_publisher.py")


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    import importlib
    import sys

    parser = argparse.ArgumentParser(description="Generate knot test fixtures for a tier.")
    parser.add_argument("tier", help="Tier name: A1 | B2 | C2 | ...")
    args = parser.parse_args()

    try:
        cfg = getattr(importlib.import_module("tests.fixtures.tier_configs"), args.tier)
    except AttributeError:
        print(f"Unknown tier: {args.tier}", file=sys.stderr)
        sys.exit(1)

    out = Path(__file__).parent / args.tier
    FixtureGenerator(cfg).write(out)
    print(f"Generated {args.tier} → {out}")
