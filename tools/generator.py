"""Synthetic fixture generator for knot test tiers.

Each tier is fully deterministic: re-running with the same TierConfig
produces byte-identical output. All randomness is derived from tier.seed.
"""

from __future__ import annotations

import csv
import hashlib
import io
import random
import textwrap
from pathlib import Path
from typing import Any


def _stable_hash(s: str) -> int:
    """Deterministic hash for seed derivation — immune to PYTHONHASHSEED."""
    return int.from_bytes(hashlib.sha256(s.encode()).digest()[:8], "little")

from tools.tier_configs import TierConfig


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
                src_seed = self.tier.seed ^ _stable_hash(f"{cls_name}:{source_name}") & 0xFFFFFFFF
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
# B2 data pools
# ---------------------------------------------------------------------------

_B2_MOVIE_TITLES = [
    "The Matrix", "The Sixth Sense", "Fight Club", "American Beauty",
    "The Green Mile", "Magnolia", "Eyes Wide Shut", "The Insider",
    "Being John Malkovich", "Three Kings", "Office Space", "Election",
    "Rushmore", "The Limey", "Go", "Run Lola Run", "Dark City",
    "Pleasantville", "Pi", "Rounders", "Lock Stock and Two Smoking Barrels",
    "Blade", "Buffalo 66", "The Truman Show", "Saving Private Ryan",
    "The Big Lebowski", "Happiness", "Velvet Goldmine", "A Simple Plan",
    "The Negotiator", "Armageddon", "Snake Eyes", "Meet Joe Black",
    "Patch Adams", "The Waterboy", "You've Got Mail", "Ronin",
    "Enemy of the State", "Deep Impact", "City of Angels", "Sphere",
    "Lethal Weapon 4", "The Mask of Zorro", "Blade Runner", "Gattaca",
    "Contact", "L.A. Confidential", "Boogie Nights", "Heat", "Se7en",
    # Unicode/non-Latin titles (cat 1.6, 1.7)
    "Amélie", "Das Boot", "La Dolce Vita", "8½", "Rashōmon",
    "七人の侍", "Криминальное чтиво", "مدير المدرسة",
    # En-dash/em-dash encoding variants (cat 1.10)
    "Spider–Man", "Spider—Man", "Spider-Man",
    "Avengers–Infinity War", "Avengers—Infinity War",
    # More titles to reach 100
    "Good Will Hunting", "As Good as It Gets", "The Full Monty",
    "Donnie Brasco", "Fargo", "Trainspotting", "Pulp Fiction",
    "The Silence of the Lambs", "Schindler's List", "Jurassic Park",
    "Forrest Gump", "The Shawshank Redemption", "Goodfellas",
    "The Godfather", "Casablanca", "Citizen Kane", "Vertigo",
    "Rear Window", "North by Northwest", "Psycho",
    "2001: A Space Odyssey", "A Clockwork Orange", "Dr. Strangelove",
    "The Shining", "Full Metal Jacket",
]

_B2_PERSON_NAMES = [
    # ASCII names
    "Tom Hanks", "Meryl Streep", "Jack Nicholson", "Cate Blanchett",
    "Denzel Washington", "Jodie Foster", "Robert De Niro", "Natalie Portman",
    "Al Pacino", "Kate Winslet", "Dustin Hoffman", "Halle Berry",
    "Gene Hackman", "Charlize Theron", "Clint Eastwood", "Julia Roberts",
    "Anthony Hopkins", "Hillary Swank", "Morgan Freeman", "Reese Witherspoon",
    # Unicode diacritics (cat 1.6)
    "Alejandro González Iñárritu", "Penélope Cruz", "Søren Kierkegaard",
    "François Truffaut", "Jean-Luc Godard", "Agnès Varda",
    "Björk Guðmundsdóttir", "Věra Chytilová", "Andrzej Wajda",
    "Krzysztof Kieślowski", "Ingmar Bergman", "Märta Torén",
    # Non-Latin scripts (cat 1.7)
    "黒澤 明",          # Kurosawa Akira (CJK)
    "宮崎 駿",          # Miyazaki Hayao (CJK)
    "Андрей Тарковский",  # Andrei Tarkovsky (Cyrillic)
    "Сергей Эйзенштейн",  # Sergei Eisenstein (Cyrillic)
    "عمر الشريف",       # Omar Sharif (Arabic)
    "يوسف شاهين",       # Youssef Chahine (Arabic)
    # More names to reach 200
    "Steven Spielberg", "Martin Scorsese", "Francis Ford Coppola",
    "Stanley Kubrick", "Alfred Hitchcock", "Billy Wilder", "John Ford",
    "Howard Hawks", "Orson Welles", "Jean Renoir", "Federico Fellini",
    "Michelangelo Antonioni", "Luchino Visconti", "Pier Paolo Pasolini",
    "Werner Herzog", "Rainer Werner Fassbinder", "Wim Wenders",
    "Akira Kurosawa", "Yasujirō Ozu", "Kenji Mizoguchi",
    "Wong Kar-wai", "Zhang Yimou", "Chen Kaige", "Hou Hsiao-hsien",
    "Abbas Kiarostami", "Mohsen Makhmalbaf", "Jafar Panahi",
    "Pedro Almodóvar", "Carlos Saura", "Víctor Erice",
    "Michael Haneke", "Lars von Trier", "Thomas Vinterberg",
    "Joel Coen", "Ethan Coen", "David Lynch", "David Fincher",
    "Paul Thomas Anderson", "Wes Anderson", "Sofia Coppola",
    "Quentin Tarantino", "Christopher Nolan", "Darren Aronofsky",
    "Spike Jonze", "Michel Gondry", "Charlie Kaufman",
    "Richard Linklater", "Jim Jarmusch", "Todd Haynes",
    "Gus Van Sant", "Todd Solondz", "Mary Harron",
    "Kathryn Bigelow", "Jane Campion", "Lynne Ramsay",
    "Andrea Arnold", "Kelly Reichardt", "Debra Granik",
    "Ava DuVernay", "Ryan Coogler", "Barry Jenkins",
    "Jordan Peele", "Dee Rees", "Chloé Zhao",
    "Steve McQueen", "Amma Asante", "Yann Demange",
    "Sam Mendes", "Danny Boyle", "Mike Leigh",
    "Ken Loach", "Stephen Frears", "Neil Jordan",
    "John Boorman", "Nicolas Roeg", "Lindsay Anderson",
    "Derek Jarman", "Terence Davies", "Sally Potter",
    "Peter Greenaway", "Mike Figgis", "Ridley Scott",
    "Tony Scott", "Alan Parker", "Adrian Lyne",
    "John Schlesinger", "Karel Reisz", "Lindsay Anderson",
    "Alexander Mackendrick", "Carol Reed", "David Lean",
    "Anthony Asquith", "Michael Powell", "Emeric Pressburger",
    "Humphrey Jennings", "Alberto Cavalcanti", "Thorold Dickinson",
    "Charles Crichton", "Henry Cornelius", "Basil Dearden",
    "Ronald Neame", "Guy Hamilton", "John Guillermin",
    "Jack Clayton", "Bryan Forbes", "Joseph Losey",
    "John Huston", "William Wyler", "George Stevens",
    "Fred Zinnemann", "Robert Wise", "Sidney Lumet",
    "John Cassavetes", "Arthur Penn", "Sam Peckinpah",
    "Don Siegel", "Robert Altman", "Hal Ashby",
    "Monte Hellman", "Jerry Schatzberg", "Bob Rafelson",
    "John Schlesinger", "Mike Nichols", "Bob Fosse",
    "Alan J. Pakula", "Peter Bogdanovich", "Brian De Palma",
    "William Friedkin", "Francis Ford Coppola", "Martin Scorsese",
    "Woody Allen", "Elaine May", "Joan Micklin Silver",
    "Barbara Loden", "Claudia Weill", "Joyce Chopra",
    "Susan Seidelman", "Martha Coolidge", "Penny Marshall",
    "Amy Heckerling", "Mary Lambert", "Kathryn Bigelow",
    "Mimi Leder", "Betty Thomas", "Nora Ephron",
    "Nancy Meyers", "Nora Ephron", "Jodie Foster",
]

_B2_BIRTHDATE_FORMATS = [
    "{year}-{month:02d}-{day:02d}",            # ISO format
    "{month:02d}/{day:02d}/{year}",             # US format
    "{day:02d}/{month:02d}/{year}",             # EU format
    "{month_abbr} {day} {year}",               # "Mar 31 1999"
    "{day} {month_abbr} {year}",               # "31 Mar 1999"
]

_MONTH_ABBRS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

_B2_ROLES = ["director", "actor", "writer", "producer", "composer"]

# Mixed-case variants for role seeding (cat 1.5)
_B2_ROLE_MIXED_CASE = {
    "director": ["director", "Director", "DIRECTOR", "dIRECTOR"],
    "actor":    ["actor",    "Actor",    "ACTOR",    "aCtOr"],
    "writer":   ["writer",   "Writer",   "WRITER",   "wRITER"],
    "producer": ["producer", "Producer", "PRODUCER", "pRODUCER"],
    "composer": ["composer", "Composer", "COMPOSER", "cOMPOSER"],
}

_B2_COUNTRIES = ["US", "GB", "FR", "DE", "IT", "JP", "CN", "RU", "BR", "MX"]

_B2_WIKIDATA_PREFIX = "Q"


def _b2_birthdate(rng: random.Random, fmt_idx: int | None = None) -> str:
    """Generate a birthdate string in one of the 5 format variants (cat 1.8)."""
    year = rng.randint(1920, 1980)
    month = rng.randint(1, 12)
    day = rng.randint(1, 28)
    idx = fmt_idx if fmt_idx is not None else rng.randint(0, len(_B2_BIRTHDATE_FORMATS) - 1)
    fmt = _B2_BIRTHDATE_FORMATS[idx]
    month_abbr = _MONTH_ABBRS[month - 1]
    return fmt.format(year=year, month=month, day=day, month_abbr=month_abbr)


# ---------------------------------------------------------------------------
# B2 CSV writers per class
# ---------------------------------------------------------------------------

def _b2_movie_rows(n: int, rng: random.Random, source_name: str) -> list[dict]:
    """Generate n Movie rows for the given source with B2 edge cases seeded."""
    rows = []
    n_titles = len(_B2_MOVIE_TITLES)
    for i in range(n):
        imdb_id = f"tt{2000000 + i:07d}"
        title = _B2_MOVIE_TITLES[i % n_titles]
        year = rng.randint(1985, 2005)
        runtime = rng.randint(80, 220)
        rating = round(rng.uniform(4.5, 9.8), 1)
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

    # --- Designed-in edge cases ---

    # cat 1.1: null optional slots (~5 rows)
    for idx in _spread_indices(n, 5, rng):
        rows[idx] = dict(rows[idx])
        if rng.random() < 0.5:
            rows[idx]["runtime_minutes"] = ""
        else:
            rows[idx]["rating"] = ""

    # cat 1.2: null required title (~2 rows)
    for idx in _spread_indices(n, 2, rng, reserved=True):
        rows[idx] = dict(rows[idx])
        rows[idx]["title"] = ""

    # cat 1.3: empty string vs null (~3 rows)
    for idx in _spread_indices(n, 3, rng):
        rows[idx] = dict(rows[idx])
        rows[idx]["rating"] = "0.0"

    # cat 1.4: whitespace in title (~3 rows)
    for idx in _spread_indices(n, 3, rng):
        rows[idx] = dict(rows[idx])
        rows[idx]["title"] = "  " + rows[idx]["title"] + "   "

    # cat 1.9: numeric edge values (~2 rows)
    for idx in _spread_indices(n, 2, rng):
        rows[idx] = dict(rows[idx])
        rows[idx]["year"] = "0"

    # cat 1.11: trailing whitespace in cells (~5 rows)
    for idx in _spread_indices(n, 5, rng):
        rows[idx] = dict(rows[idx])
        rows[idx]["genres"] = rows[idx]["genres"] + "   "

    # cat 1.13: missing identifier (~2 rows)
    for idx in _spread_indices(n, 2, rng, reserved=True):
        rows[idx] = dict(rows[idx])
        rows[idx]["imdb_id"] = ""

    # cat 3.2/3.3: disagreement — year offset between sources
    if "tmdb" in source_name or "wikidata" in source_name:
        for idx in _spread_indices(n, 10, rng):
            rows[idx] = dict(rows[idx])
            offset = 1 if "tmdb" in source_name else 2
            try:
                yr = int(rows[idx]["year"]) if rows[idx]["year"] else 1990
                rows[idx]["year"] = str(yr + offset)
            except ValueError:
                rows[idx]["year"] = "1992"

    # cat 1.10: encoding variants (en-dash/em-dash in titles)
    if "wikidata" in source_name:
        for idx in _spread_indices(n, 3, rng):
            rows[idx] = dict(rows[idx])
            rows[idx]["title"] = rows[idx]["title"].replace("-", "–")

    # cat 1.6: Unicode diacritics already in _B2_MOVIE_TITLES via Amélie etc.
    # cat 1.7: non-Latin scripts already in _B2_MOVIE_TITLES via CJK/Cyrillic/Arabic

    return rows


def _b2_person_rows(n: int, rng: random.Random, source_name: str) -> list[dict]:
    """Generate n Person rows for the given source."""
    rows = []
    n_names = len(_B2_PERSON_NAMES)
    for i in range(n):
        imdb_id = f"nm{3000000 + i:07d}"
        name = _B2_PERSON_NAMES[i % n_names]
        # Birthdate format varies by source (cat 1.8)
        fmt_idx = 0 if "imdb" in source_name else (1 if "tmdb" in source_name else 2)
        birthdate = _b2_birthdate(rng, fmt_idx=fmt_idx)
        country = rng.choice(_B2_COUNTRIES)
        wikidata_id = f"Q{7000000 + i}" if rng.random() > 0.3 else ""
        rows.append({
            "imdb_id": imdb_id,
            "name": name,
            "birthdate": birthdate,
            "country": country,
            "wikidata_id": wikidata_id,
        })

    # cat 1.1: null optional birthdate
    for idx in _spread_indices(n, 5, rng):
        rows[idx] = dict(rows[idx])
        rows[idx]["birthdate"] = ""

    # cat 1.4: whitespace in name
    for idx in _spread_indices(n, 3, rng):
        rows[idx] = dict(rows[idx])
        rows[idx]["name"] = "  " + rows[idx]["name"] + "  "

    # cat 1.13: missing imdb_id
    for idx in _spread_indices(n, 2, rng, reserved=True):
        rows[idx] = dict(rows[idx])
        rows[idx]["imdb_id"] = ""

    return rows


def _b2_credit_rows(n: int, n_movies: int, n_persons: int, rng: random.Random,
                    source_name: str) -> list[dict]:
    """Generate n Credit rows linking movies to persons."""
    rows = []
    for i in range(n):
        credit_id = f"cr_{source_name}_{i:06d}"
        movie_idx = rng.randint(0, n_movies - 1)
        person_idx = rng.randint(0, n_persons - 1)
        imdb_movie_id = f"tt{2000000 + movie_idx:07d}"
        imdb_person_id = f"nm{3000000 + person_idx:07d}"
        role = rng.choice(_B2_ROLES)
        rows.append({
            "credit_id": credit_id,
            "movie_imdb_id": imdb_movie_id,
            "person_imdb_id": imdb_person_id,
            "role": role,
        })

    # cat 1.5: mixed-case role values
    for idx in _spread_indices(n, 15, rng):
        rows[idx] = dict(rows[idx])
        r = rows[idx]["role"]
        rows[idx]["role"] = rng.choice(_B2_ROLE_MIXED_CASE.get(r, [r]))

    # cat 4.3: orphan references (movie/person id that doesn't exist)
    if "wikidata" in source_name:
        for idx in _spread_indices(n, 5, rng):
            rows[idx] = dict(rows[idx])
            rows[idx]["movie_imdb_id"] = f"tt{9999000 + idx:07d}"  # nonexistent

    return rows


def _spread_indices(n: int, count: int, rng: random.Random,
                    reserved: bool = False) -> list[int]:
    """Return `count` well-spread row indices within [0, n)."""
    step = max(1, n // (count + 1))
    indices = []
    used: set[int] = set()
    for j in range(count):
        candidate = (j + 1) * step + rng.randint(0, max(0, step - 1))
        candidate = candidate % n
        if reserved:
            while candidate in used:
                candidate = (candidate + 1) % n
            used.add(candidate)
        indices.append(candidate)
    return indices


def _write_b2_movie_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["imdb_id", "title", "year", "runtime_minutes", "rating", "genres"]
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames, lineterminator="\n",
                             extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    path.write_text(buf.getvalue(), encoding="utf-8")


def _write_b2_person_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["imdb_id", "name", "birthdate", "country", "wikidata_id"]
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames, lineterminator="\n",
                             extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    path.write_text(buf.getvalue(), encoding="utf-8")


def _write_b2_credit_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["credit_id", "movie_imdb_id", "person_imdb_id", "role"]
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames, lineterminator="\n",
                             extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    path.write_text(buf.getvalue(), encoding="utf-8")


def _b2_expected_facts(path: Path, movie_rows: list[dict]) -> None:
    """Write B2 expected_facts.yaml with ~30 ground-truth assertions."""
    path.parent.mkdir(parents=True, exist_ok=True)
    # Pick clean rows (non-empty imdb_id and title)
    clean = [r for r in movie_rows
             if r.get("imdb_id") and r.get("title") and r.get("title").strip()][:30]

    lines = [
        "# B2 ground-truth assertions.",
        "# Covers: multi-source trust resolution, derived edges, cross-class refs.",
        "# Categories: 2.x (ER), 3.x (trust), 4.x (relations), 5.x (derivations),",
        "#             12.8 (replay determinism), 15.x (audit walk-back).",
        "movies:",
    ]
    for row in clean:
        cid = f"mov_{row['imdb_id']}"
        title = row["title"].strip()
        year = row["year"] if row.get("year") else "null"
        runtime = row["runtime_minutes"] if row.get("runtime_minutes") else "null"
        rating = row["rating"] if row.get("rating") else "null"
        raw_genres = (row.get("genres") or "").strip()
        genres_list = [f'"{g.strip()}"' for g in raw_genres.split("|") if g.strip()]
        genres_yaml = "[" + ", ".join(genres_list) + "]" if genres_list else "[]"
        lines.append(f"  {cid}:")
        lines.append(f'    title: "{title}"')
        lines.append(f"    year: {year}")
        lines.append(f"    runtime_minutes: {runtime}")
        lines.append(f"    rating: {rating}")
        lines.append(f"    genres: {genres_yaml}")
        lines.append(f"    # derived edges (cat 5.2, 5.3):")
        lines.append(f"    director: null  # resolved at test time from Credit rows")
        lines.append(f"    actors: []")

    # Known dupes — 20 pairs with ground-truth merge labels (cat 2.1, 2.3)
    lines.append("")
    lines.append("# Known-dupe merge assertions (cat 2.1, 2.3):")
    lines.append("known_dupes:")
    for i in range(20):
        imdb_id = f"tt{2000000 + i:07d}"
        lines.append(f"  - canonical_id: mov_{imdb_id}")
        lines.append(f"    sources: [imdb_movies, tmdb_movies]")
        lines.append(f"    ground_truth: same_entity")

    # Known non-dupes (cat 2.2)
    lines.append("")
    lines.append("# Known non-dupe assertions (cat 2.2):")
    lines.append("known_non_dupes:")
    for i in range(10):
        a = f"tt{2000050 + i:07d}"
        b = f"tt{2000070 + i:07d}"
        lines.append(f"  - canonical_id_a: mov_{a}")
        lines.append(f"    canonical_id_b: mov_{b}")
        lines.append(f"    ground_truth: different_entity")

    # UNIQUE_OR_FAIL violation candidates (cat 3.6)
    lines.append("")
    lines.append("# UNIQUE_OR_FAIL violation candidates (cat 3.6):")
    lines.append("unique_or_fail_violations:")
    for i in range(5):
        imdb_id = f"tt{2000090 + i:07d}"
        lines.append(f"  - canonical_id: mov_{imdb_id}")
        lines.append(f"    slot: imdb_id")
        lines.append(f"    expected: UniqueOrFailError")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _b2_edge_cases(path: Path) -> None:
    """Write B2 edge_cases.yaml mapping all B2-tier categories to seeded cases."""
    path.parent.mkdir(parents=True, exist_ok=True)
    content = textwrap.dedent("""\
        # B2 edge-case manifest — all categories from EDGE-CASES.md seeded.
        # Format: case-id → seeded canonical_id(s) and seeded values.

        "1.1":
          description: "Null values in optional slots"
          seeded_in: imdb_movies, tmdb_movies, wikidata_movies
          example_canonical_id: mov_tt2000005
          slot: runtime_minutes
          seeded_value: null
          expected: normalized

        "1.2":
          description: "Null values in required slots → fail validation"
          seeded_in: imdb_movies
          example_canonical_id: mov_tt2000012
          slot: title
          seeded_value: null
          expected: validation_error

        "1.3":
          description: "Empty string vs null (semantically distinct)"
          seeded_in: imdb_movies
          example_canonical_id: mov_tt2000020
          slot: rating
          seeded_value: "0.0"
          expected: normalized

        "1.4":
          description: "Leading/trailing/internal multiple whitespace"
          seeded_in: imdb_movies
          example_canonical_id: mov_tt2000030
          slot: title
          seeded_value: "  The Matrix   "
          expected: normalized

        "1.5":
          description: "Mixed case for enum values (DIRECTOR / Director / director / dIRECTOR)"
          seeded_in: tmdb_credits, wikidata_credits
          example_canonical_id: cr_tmdb_credits_000050
          slot: role
          seeded_value: "DIRECTOR"
          expected: normalized_to_director

        "1.6":
          description: "Unicode names with diacritics (Søren, Peña, Wachowski)"
          seeded_in: imdb_persons, tmdb_persons
          example_canonical_id: per_nm3000022
          slot: name
          seeded_value: "Alejandro González Iñárritu"
          expected: normalized

        "1.7":
          description: "Non-Latin scripts (Cyrillic, CJK, Arabic)"
          seeded_in: imdb_persons
          example_canonical_id: per_nm3000030
          slot: name
          seeded_value: "黒澤 明"
          expected: normalized

        "1.8":
          description: "Date format drift across sources"
          seeded_in: imdb_persons, tmdb_persons
          example_canonical_ids: [per_nm3000010, per_nm3000010]
          slot: birthdate
          seeded_values: ["1943-07-14", "Jul 14 1943"]
          expected: normalized_to_iso

        "1.9":
          description: "Numeric edge values (year=0, runtime=0)"
          seeded_in: imdb_movies
          example_canonical_id: mov_tt2000040
          slot: year
          seeded_value: "0"
          expected: normalized

        "1.10":
          description: "Encoding variants (en-dash vs em-dash vs hyphen)"
          seeded_in: wikidata_movies
          example_canonical_id: mov_tt2000051
          slot: title
          seeded_value: "Spider\\u2013Man"
          expected: normalized_to_hyphen

        "1.11":
          description: "Trailing whitespace in CSV cells"
          seeded_in: imdb_movies
          example_canonical_id: mov_tt2000008
          slot: genres
          seeded_value: "Action|Drama   "
          expected: normalized

        "1.12":
          description: "Source rows with same identifier but conflicting payloads"
          seeded_in: imdb_movies, tmdb_movies
          example_canonical_id: mov_tt2000015
          slot: year
          seeded_values: ["1994", "1995"]
          expected: resolved_by_argmax_trust

        "1.13":
          description: "Source rows with missing identifier → drop"
          seeded_in: imdb_movies
          example_canonical_id: null
          slot: imdb_id
          seeded_value: null
          expected: drop

        "2.1":
          description: "Known dupes — different source IDs, same entity → must merge"
          seeded_in: imdb_movies, tmdb_movies
          canonical_ids: [mov_tt2000000, mov_tt2000001, mov_tt2000002]
          ground_truth: same_entity

        "2.2":
          description: "Known non-dupes — similar names, different entities → must NOT merge"
          seeded_in: imdb_movies, tmdb_movies
          canonical_ids: [mov_tt2000050, mov_tt2000070]
          ground_truth: different_entity

        "2.3":
          description: "Three-way dupes (same Movie in IMDB + TMDB + Wikidata)"
          seeded_in: imdb_movies, tmdb_movies, wikidata_movies
          canonical_ids: [mov_tt2000005]
          ground_truth: same_entity

        "2.4":
          description: "Asymmetric coverage (Movie in IMDB+TMDB but missing from Wikidata)"
          seeded_in: wikidata_movies (absence)
          canonical_ids: [mov_tt2000060, mov_tt2000061]
          ground_truth: present_in_imdb_tmdb_absent_in_wikidata

        "2.5":
          description: "Surfaced conflicts (review-by-default)"
          seeded_in: imdb_movies, tmdb_movies
          example_canonical_id: mov_tt2000015
          conflict_slot: year
          expected: surfaced_for_review

        "2.6":
          description: "Forced merges via _user_er_decisions"
          canonical_ids: [mov_tt2000080, mov_tt2000081]
          expected: forced_merge

        "2.7":
          description: "Forced non-merges via _user_er_decisions"
          canonical_ids: [mov_tt2000082, mov_tt2000083]
          expected: forced_non_merge

        "3.1":
          description: "Full agreement (3 sources same value)"
          example_canonical_id: mov_tt2000000
          slot: title
          expected: degenerate_agree

        "3.2":
          description: "2-vs-1 majority (MODE wins)"
          example_canonical_id: mov_tt2000010
          slot: year
          seeded_values: ["1994", "1994", "1995"]
          expected: mode_wins

        "3.3":
          description: "3 distinct values (no MODE winner; ARGMAX_TRUST tiebreak)"
          example_canonical_id: mov_tt2000011
          slot: year
          seeded_values: ["1993", "1994", "1995"]
          expected: argmax_trust_tiebreak

        "3.5":
          description: "Source contributes null vs absent"
          example_canonical_id: mov_tt2000005
          slot: runtime_minutes
          expected: null_vs_absent_distinct

        "3.6":
          description: "UNIQUE_OR_FAIL policy violation → typed exception"
          example_canonical_id: mov_tt2000090
          slot: imdb_id
          expected: UniqueOrFailError

        "3.7":
          description: "MEDIAN_NUMERIC on integer slot"
          example_canonical_id: mov_tt2000000
          slot: runtime_minutes
          seeded_values: ["95", "105", "110"]
          expected: median_105

        "3.8":
          description: "LATEST_WATERMARK with backdated asserted_at"
          example_canonical_id: mov_tt2000020
          slot: rating
          expected: latest_watermark_wins

        "3.9":
          description: "WEIGHTED_VOTE with high-trust outlier vs majority"
          example_canonical_id: mov_tt2000025
          slot: year
          expected: weighted_vote_resolution

        "3.10":
          description: "Each ResolutionPolicy enum value exercised at least once"
          slots:
            ARGMAX_TRUST: movie.title
            MODE: movie.genres
            MEDIAN_NUMERIC: movie.runtime_minutes
            LATEST_WATERMARK: movie.rating (seeded with backdated run)
            UNIQUE_OR_FAIL: movie.imdb_id
            WEIGHTED_VOTE: movie.year (seeded row)

        "4.1":
          description: "Credit references existing Movie + Person"
          example_canonical_id: cr_imdb_credits_000000
          slots: [movie_imdb_id, person_imdb_id]
          expected: fk_resolves

        "4.2":
          description: "Reference through merged canonical_id (lineage redirect)"
          pre_merge_canonical_id: mov_tt2000000_src_tmdb
          post_merge_canonical_id: mov_tt2000000
          expected: lineage_redirect

        "4.3":
          description: "Orphan reference (parent doesn't exist) → loud failure"
          seeded_in: wikidata_credits
          example_canonical_id: cr_wikidata_credits_000000
          slot: movie_imdb_id
          seeded_value: tt9999000
          expected: OrphanReferenceError

        "4.6":
          description: "Cross-class pinning composes with ER post-merge state"
          expected: credit_stage_pins_to_movie_person_run_hashes

        "5.1":
          description: "Empty derivation result (no matching Credit rows for a Movie)"
          example_canonical_id: mov_tt2000095
          derived_slot: director
          expected: empty_list

        "5.2":
          description: "Single-result derivation (one director per movie)"
          example_canonical_id: mov_tt2000000
          derived_slot: director
          expected: single_person_canonical_id

        "5.3":
          description: "Multi-result derivation (2+ actors per movie)"
          example_canonical_id: mov_tt2000000
          derived_slot: actors
          expected: list_of_person_canonical_ids

        "5.6":
          description: "Forward-chained at materialization (Neo4j edges from Credit)"
          expected: neo4j_DIRECTED_BY_edge_exists

        "5.7":
          description: "Backward-chained at translator (consumer query → SQL JOIN)"
          example_query: "Movie.director.country == 'US'"
          expected: join_through_credit_person_country

        "8.2":
          description: "Splits — previously-merged canonical_id needs to split (mutation)"
          expected: mutation_test_only

        "9.1":
          description: "Source watermark advances → only that source cache-misses (mutation)"
          expected: mutation_test_only

        "11.4":
          description: "DataContext primary with duplicate classes → dedup or fail"
          expected: dedup_or_loud_failure

        "11.7":
          description: "Multi-class primary — spec.classes variant"
          impl: iceberg_publisher.py
          expected: all_classes_written

        "11.8":
          description: "DerivedSlot as primary → edge view"
          impl: neo4j_publisher.py
          derived_slot: movie_director
          expected: edge_view_emitted

        "11.9":
          description: "DataContext joins via slot.range traversal"
          example: "Movie.director.name join"
          expected: join_compiled

        "12.8":
          description: "Replay of a compile hash produces byte-identical output"
          expected: deterministic_replay

        "13.1":
          description: "POST /runs/Movie (per-class trigger)"
          expected: movie_pipeline_runs

        "13.2":
          description: "POST /runs/full (whole-pipeline trigger; toposort)"
          expected: full_toposort_run

        "13.3":
          description: "POST /runs/<stage> (single-stage trigger)"
          expected: single_stage_runs

        "14.1":
          description: "Single-class materialization (Movie → Iceberg analytics table)"
          impl: iceberg_publisher.py
          expected: iceberg_table_written

        "14.2":
          description: "Multi-class materialization (whole graph → Neo4j)"
          impl: neo4j_publisher.py
          expected: all_nodes_written

        "14.3":
          description: "Multi-target — one impl writes Neo4j + Iceberg + parquet"
          expected: multi_target_write

        "14.4":
          description: "Derived-edge materialization (Movie.director → :DIRECTED_BY)"
          impl: neo4j_publisher.py
          expected: directed_by_edge_exists

        "14.6":
          description: "Materialization with Config.exclude_classes set"
          impl: neo4j_publisher.py
          expected: excluded_class_absent_from_output

        "15.1":
          description: "Neo4j fact → run → compile_hash → spec_revs → contributing source rows"
          expected: full_audit_walk

        "15.2":
          description: "Walk-back across ER merge (canonical_id_lineage redirect)"
          expected: lineage_redirect_walk

        "15.3":
          description: "Walk-back for derived slot"
          expected: director_credit_person_walk

        "15.4":
          description: "Walk-back across cross-class pinning"
          expected: credit_movie_person_walk

        "15.5":
          description: "Walk-back when source has multiple watermarks across runs"
          expected: watermark_history_walk

        "15.6":
          description: "Walk-back for a fact that was overlay-corrected"
          expected: correction_overlay_walk
    """)
    path.write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# B2 generator
# ---------------------------------------------------------------------------

class B2FixtureGenerator:
    """Generator for the B2 integration tier (Movie + Person + Credit, 3 sources each)."""

    def __init__(self, tier: TierConfig) -> None:
        self.tier = tier

    def _rng_for(self, cls_name: str, source_name: str) -> random.Random:
        """Deterministic per-(class, source) RNG derived from tier seed."""
        derived = self.tier.seed ^ (_stable_hash(f"{cls_name}:{source_name}") & 0xFFFFFFFF)
        return random.Random(derived)

    def write(self, out_dir: Path) -> None:
        out_dir.mkdir(parents=True, exist_ok=True)

        n_movies = self.tier.base_volumes["Movie"]
        n_persons = self.tier.base_volumes["Person"]
        n_credits = self.tier.base_volumes["Credit"]

        # Movie CSVs (3 sources)
        movie_sources = self.tier.sources_per_class.get("Movie", [])
        first_movie_rows: list[dict] = []
        for src in movie_sources:
            rng = self._rng_for("Movie", src)
            rows = _b2_movie_rows(n_movies, rng, src)
            _write_b2_movie_csv(out_dir / "sources" / f"{src}.csv", rows)
            if not first_movie_rows:
                first_movie_rows = rows

        # Person CSVs (2 sources for B2)
        person_sources = self.tier.sources_per_class.get("Person", [])
        for src in person_sources:
            rng = self._rng_for("Person", src)
            rows = _b2_person_rows(n_persons, rng, src)
            _write_b2_person_csv(out_dir / "sources" / f"{src}.csv", rows)

        # Credit CSVs (3 sources)
        credit_sources = self.tier.sources_per_class.get("Credit", [])
        for src in credit_sources:
            rng = self._rng_for("Credit", src)
            rows = _b2_credit_rows(n_credits, n_movies, n_persons, rng, src)
            _write_b2_credit_csv(out_dir / "sources" / f"{src}.csv", rows)

        # expected_facts.yaml
        _b2_expected_facts(out_dir / "expected_facts.yaml", first_movie_rows)

        # edge_cases.yaml
        _b2_edge_cases(out_dir / "edge_cases.yaml")

        # Preserve hand-authored spec.py and impls
        if not (out_dir / "spec.py").exists():
            (out_dir / "spec.py").write_text(
                "# Hand-authored — see B2/spec.py\n", encoding="utf-8"
            )


# ---------------------------------------------------------------------------
# C2 data pools
# ---------------------------------------------------------------------------

_C2_SERIES_TITLES = [
    "Breaking Bad", "The Wire", "The Sopranos", "Game of Thrones",
    "Chernobyl", "Fleabag", "Succession", "Better Call Saul",
    "Twin Peaks", "The Americans",
]

_C2_EPISODE_TITLE_PATTERNS = [
    "Pilot", "The Beginning", "Revelation", "Breaking Point",
    "No Way Out", "The End", "Crisis", "Aftermath",
    "Consequences", "Resolution",
]

_C2_GAME_TITLES = [
    "The Last of Us", "Red Dead Redemption 2", "God of War",
    "Hades", "Disco Elysium", "Outer Wilds",
    "Control", "Death Stranding", "Sekiro", "Ghost of Tsushima",
]

_C2_PLATFORMS = ["PC", "PS5", "PS4", "Xbox Series X", "Xbox One", "Switch", "iOS", "Android"]

_C2_STUDIO_NAMES = [
    "Paramount Pictures", "Warner Bros.", "Sony Pictures",
    "Universal Pictures", "Walt Disney Pictures",
]

_C2_AWARD_NAMES = [
    "Best Picture", "Best Director", "Best Actor", "Best Actress",
    "Best Supporting Actor", "Best Supporting Actress",
    "Best Screenplay", "Best Cinematography",
    "Best Visual Effects", "Best Score",
    "Best Animated Feature", "Best Documentary",
    "Best Foreign Language Film", "Best Editing",
    "Best Sound", "Best Production Design",
    "Best Costume Design", "Best Makeup and Hairstyling",
    "Best Short Film", "Best Live Action Short",
]

_C2_COUNTRY_CODES = [
    "US", "GB", "FR", "DE", "IT", "JP", "CN", "RU",
    "BR", "MX",
]

_C2_COUNTRY_NAMES = {
    "US": "United States",
    "GB": "United Kingdom",
    "FR": "France",
    "DE": "Germany",
    "IT": "Italy",
    "JP": "Japan",
    "CN": "China",
    "RU": "Russia",
    "BR": "Brazil",
    "MX": "Mexico",
}


def _c2_movie_rows(n: int, rng: random.Random, source_name: str) -> list[dict]:
    """C2 Movie rows — same shape as B2 plus cat 1.7/1.10 seeding."""
    rows = []
    for i in range(n):
        imdb_id = f"tt{4000000 + i:07d}"
        title = _B2_MOVIE_TITLES[i % len(_B2_MOVIE_TITLES)]
        year = rng.randint(1985, 2023)
        runtime = rng.randint(80, 220)
        rating = round(rng.uniform(4.5, 9.8), 1)
        g1 = rng.choice(_GENRES_POOL)
        g2 = rng.choice([g for g in _GENRES_POOL if g != g1])
        genres = f"{g1}|{g2}" if rng.random() > 0.3 else g1
        rows.append({
            "imdb_id": imdb_id,
            "primary_title": title,
            "original_title": title,
            "year": str(year),
            "runtime_minutes": str(runtime),
            "rating": str(rating),
            "genres": genres,
        })

    # cat 1.1: null optional
    for idx in _spread_indices(n, 3, rng):
        rows[idx] = dict(rows[idx])
        rows[idx]["runtime_minutes"] = ""

    # cat 1.7: non-Latin original titles
    for idx in _spread_indices(n, 3, rng):
        rows[idx] = dict(rows[idx])
        rows[idx]["original_title"] = rng.choice(["七人の侍", "Криминальное чтиво", "مدير المدرسة"])

    # cat 1.10: en-dash encoding in wikidata source
    if "wikidata" in source_name:
        for idx in _spread_indices(n, 2, rng):
            rows[idx] = dict(rows[idx])
            rows[idx]["primary_title"] = rows[idx]["primary_title"].replace("-", "–")

    return rows


def _c2_series_rows(n: int, rng: random.Random, source_name: str) -> list[dict]:
    """C2 Series rows. imdb_series also carries discriminator-routed movie rows (cat 1.14)."""
    rows = []
    for i in range(n):
        imdb_id = f"tt{5000000 + i:07d}"
        title = _C2_SERIES_TITLES[i % len(_C2_SERIES_TITLES)]
        seasons = rng.randint(1, 8)
        episodes = seasons * rng.randint(6, 13)
        kind = "series"  # discriminator
        rows.append({
            "imdb_id": imdb_id,
            "primary_title": title,
            "original_title": title,
            "runtime_minutes": str(rng.randint(20, 60)),
            "season_count": str(seasons),
            "episode_count": str(episodes),
            "kind": kind,
        })

    # cat 1.14: discriminator-routed rows — some rows carry kind='movie' in imdb_series
    if "imdb" in source_name:
        for idx in _spread_indices(n, 3, rng):
            rows[idx] = dict(rows[idx])
            rows[idx]["kind"] = "movie"
            rows[idx]["imdb_id"] = f"tt{4000500 + idx:07d}"

    # cat 1.15: wildcard-drop rows in wikidata_series
    if "wikidata" in source_name:
        for idx in _spread_indices(n, 2, rng):
            rows[idx] = dict(rows[idx])
            rows[idx]["kind"] = "exclude"

    return rows


def _c2_episode_rows(n: int, n_series: int, rng: random.Random, _source_name: str) -> list[dict]:
    """C2 Episode rows with parent_series FK."""
    rows = []
    for i in range(n):
        imdb_id = f"tt{6000000 + i:07d}"
        series_idx = rng.randint(0, n_series - 1)
        parent_series_id = f"tt{5000000 + series_idx:07d}"
        season = rng.randint(1, 5)
        episode = rng.randint(1, 13)
        title_pattern = _C2_EPISODE_TITLE_PATTERNS[i % len(_C2_EPISODE_TITLE_PATTERNS)]
        rows.append({
            "imdb_id": imdb_id,
            "primary_title": f"S{season:02d}E{episode:02d} - {title_pattern}",
            "original_title": title_pattern,
            "runtime_minutes": str(rng.randint(22, 60)),
            "parent_series_imdb_id": parent_series_id,
            "season_number": str(season),
            "episode_number": str(episode),
        })
    return rows


def _c2_game_rows(n: int, rng: random.Random, source_name: str) -> list[dict]:
    """C2 Game rows. rawg_games carries wildcard-drop rows (cat 1.15)."""
    rows = []
    for i in range(n):
        game_id = f"game_{source_name}_{i:04d}"
        title = _C2_GAME_TITLES[i % len(_C2_GAME_TITLES)]
        year = rng.randint(2010, 2024)
        platform_count = rng.randint(1, 4)
        platforms = "|".join(rng.sample(_C2_PLATFORMS, platform_count))
        publisher = rng.choice(_C2_STUDIO_NAMES)
        kind = "game"
        rows.append({
            "game_id": game_id,
            "primary_title": title,
            "original_title": title,
            "release_year": str(year),
            "platforms": platforms,
            "publisher": publisher,
            "kind": kind,
        })

    # cat 1.15: wildcard-drop rows
    if "rawg" in source_name:
        for idx in _spread_indices(n, 2, rng):
            rows[idx] = dict(rows[idx])
            rows[idx]["kind"] = "exclude"

    return rows


def _c2_person_rows(n: int, rng: random.Random, source_name: str) -> list[dict]:
    """C2 Person rows — same as B2 Person but with 3 sources and country FK."""
    rows = []
    n_names = len(_B2_PERSON_NAMES)
    for i in range(n):
        imdb_id = f"nm{5000000 + i:07d}"
        name = _B2_PERSON_NAMES[i % n_names]
        fmt_idx = 0 if "imdb" in source_name else (1 if "tmdb" in source_name else 3)
        birthdate = _b2_birthdate(rng, fmt_idx=fmt_idx)
        country = rng.choice(_C2_COUNTRY_CODES)
        wikidata_id = f"Q{9000000 + i}" if rng.random() > 0.3 else ""
        rows.append({
            "imdb_id": imdb_id,
            "name": name,
            "birthdate": birthdate,
            "country": country,
            "wikidata_id": wikidata_id,
        })

    # cat 1.1: null optional
    for idx in _spread_indices(n, 4, rng):
        rows[idx] = dict(rows[idx])
        rows[idx]["birthdate"] = ""

    # cat 1.7: non-Latin name seeding
    for idx in _spread_indices(n, 5, rng):
        rows[idx] = dict(rows[idx])
        rows[idx]["name"] = rng.choice([
            "黒澤 明", "宮崎 駿", "Андрей Тарковский", "عمر الشريف",
        ])

    return rows


def _c2_credit_rows(n: int, n_movies: int, n_series: int, n_persons: int,
                    rng: random.Random, source_name: str) -> list[dict]:
    """C2 Credit rows — work references Title subclasses (polymorphic)."""
    rows = []
    title_kinds = ["movie", "series", "episode", "game"]
    for i in range(n):
        credit_id = f"cr_{source_name}_{i:06d}"
        person_idx = rng.randint(0, n_persons - 1)
        imdb_person_id = f"nm{5000000 + person_idx:07d}"
        # Polymorphic work reference (cat 4.4)
        kind = rng.choice(title_kinds)
        if kind == "movie":
            idx = rng.randint(0, n_movies - 1)
            work_id = f"tt{4000000 + idx:07d}"
            entity_class = "Movie"
        elif kind == "series":
            idx = rng.randint(0, n_series - 1)
            work_id = f"tt{5000000 + idx:07d}"
            entity_class = "Series"
        elif kind == "episode":
            idx = rng.randint(0, 49)
            work_id = f"tt{6000000 + idx:07d}"
            entity_class = "Episode"
        else:
            idx = rng.randint(0, 9)
            work_id = f"game_igdb_games_{idx:04d}"
            entity_class = "Game"
        role = rng.choice(_B2_ROLES)
        rows.append({
            "credit_id": credit_id,
            "person_imdb_id": imdb_person_id,
            "work_id": work_id,
            "entity_class": entity_class,
            "role": role,
        })

    # cat 1.5: mixed-case roles
    for idx in _spread_indices(n, 10, rng):
        rows[idx] = dict(rows[idx])
        r = rows[idx]["role"]
        rows[idx]["role"] = rng.choice(_B2_ROLE_MIXED_CASE.get(r, [r]))

    # cat 4.3: orphan references
    if "tmdb" in source_name:
        for idx in _spread_indices(n, 3, rng):
            rows[idx] = dict(rows[idx])
            rows[idx]["work_id"] = "tt9999999"
            rows[idx]["entity_class"] = "Movie"

    return rows


def _c2_identifier_rows(n: int, rng: random.Random, source_name: str) -> list[dict]:
    """C2 Identifier rows spanning multiple entity classes (cat 8.1)."""
    entity_classes = ["Movie", "Series", "Episode", "Game", "Person"]
    rows = []
    for i in range(n):
        identifier_id = f"id_{source_name}_{i:05d}"
        entity_class = rng.choice(entity_classes)
        if entity_class == "Movie":
            key = f"tt{4000000 + rng.randint(0, 29):07d}"
        elif entity_class == "Series":
            key = f"tt{5000000 + rng.randint(0, 9):07d}"
        elif entity_class == "Episode":
            key = f"tt{6000000 + rng.randint(0, 49):07d}"
        elif entity_class == "Game":
            key = f"game_igdb_games_{rng.randint(0, 9):04d}"
        else:
            key = f"nm{5000000 + rng.randint(0, 79):07d}"
        system = source_name.split("_")[0]  # imdb, tmdb, wikidata
        rows.append({
            "identifier_id": identifier_id,
            "entity_class": entity_class,
            "entity_src_key": key,
            "system": system,
        })
    return rows


def _c2_studio_rows(n: int, rng: random.Random, _source_name: str) -> list[dict]:
    rows = []
    for i in range(n):
        rows.append({
            "studio_id": f"studio_{i:03d}",
            "name": _C2_STUDIO_NAMES[i % len(_C2_STUDIO_NAMES)],
            "country": rng.choice(_C2_COUNTRY_CODES),
        })
    return rows


def _c2_award_rows(n: int, n_persons: int, rng: random.Random, _source_name: str) -> list[dict]:
    rows = []
    for i in range(n):
        person_idx = rng.randint(0, n_persons - 1)
        rows.append({
            "award_id": f"award_{i:04d}",
            "name": _C2_AWARD_NAMES[i % len(_C2_AWARD_NAMES)],
            "year": str(rng.randint(1990, 2024)),
            "recipient_imdb_id": f"nm{5000000 + person_idx:07d}",
        })
    return rows


def _c2_country_rows(codes: list[str]) -> list[dict]:
    return [
        {"code": code, "name": _C2_COUNTRY_NAMES[code], "region": "World"}
        for code in codes
    ]


def _write_c2_generic_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0].keys())
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames, lineterminator="\n",
                             extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    path.write_text(buf.getvalue(), encoding="utf-8")


def _c2_expected_facts(path: Path, movie_rows: list[dict], series_rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    clean_movies = [r for r in movie_rows
                    if r.get("imdb_id") and r.get("primary_title")][:10]
    clean_series = [r for r in series_rows
                    if r.get("imdb_id") and r.get("kind") == "series"][:5]

    lines = [
        "# C2 ground-truth assertions.",
        "# Covers: subclass queries, polymorphic walks, derivation chains,",
        "#         discriminator routing, wildcard-drop, audit walk-back.",
        "movies:",
    ]
    for row in clean_movies:
        cid = f"mov_{row['imdb_id']}"
        lines.append(f"  {cid}:")
        lines.append(f"    primary_title: \"{row['primary_title'].strip()}\"")
        lines.append(f"    year: {row.get('year', 'null')}")
        lines.append(f"    runtime_minutes: {row.get('runtime_minutes', 'null')}")
        lines.append(f"    # derived (cat 5.2, 5.3, 5.4):")
        lines.append(f"    director: null  # resolved from Credit at test time")
        lines.append(f"    actors: []")
        lines.append(f"    # deep chain (cat 5.4): director.country.name")
        lines.append(f"    director_country_name: null")

    lines.append("")
    lines.append("series:")
    for row in clean_series:
        cid = f"ser_{row['imdb_id']}"
        lines.append(f"  {cid}:")
        lines.append(f"    primary_title: \"{row['primary_title'].strip()}\"")
        lines.append(f"    season_count: {row.get('season_count', 'null')}")
        lines.append(f"    # derived (cat 5.2):")
        lines.append(f"    creator: null")

    lines.append("")
    lines.append("# Subclass query assertions (cat 8.3):")
    lines.append("subclass_queries:")
    lines.append("  title_query_returns_all_subclasses:")
    lines.append("    query: \"SELECT * FROM Title\"")
    lines.append("    expected_classes: [Movie, Series, Episode, Game]")

    lines.append("")
    lines.append("# Polymorphic Identifier assertions (cat 4.4, 8.1, 8.2):")
    lines.append("polymorphic_identifiers:")
    lines.append("  - identifier_id: id_imdb_identifiers_00000")
    lines.append("    entity_class: Movie")
    lines.append("    expected: resolves_to_movie_canonical_id")
    lines.append("  - identifier_id: id_imdb_identifiers_00005")
    lines.append("    entity_class: Series")
    lines.append("    expected: resolves_to_series_canonical_id")

    lines.append("")
    lines.append("# Discriminator routing (cat 1.14):")
    lines.append("discriminator_routing:")
    lines.append("  - source: imdb_series")
    lines.append("    kind_value: movie")
    lines.append("    expected: routed_to_Movie_class")
    lines.append("  - source: wikidata_series")
    lines.append("    kind_value: exclude")
    lines.append("    expected: dropped_wildcard")

    lines.append("")
    lines.append("# Deep derivation chain (cat 5.4):")
    lines.append("deep_chains:")
    lines.append("  episode_chain:")
    lines.append("    path: Episode.parent_series.creator.country.name")
    lines.append("    expected: string_country_name")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _c2_edge_cases(path: Path) -> None:
    """Write C2 edge_cases.yaml — all categories including C2-only additions."""
    path.parent.mkdir(parents=True, exist_ok=True)
    content = textwrap.dedent("""\
        # C2 edge-case manifest — ALL categories from EDGE-CASES.md at rich ontology.
        # Inherits all B2 cases plus C2-specific additions.

        "1.1":
          description: "Null values in optional slots"
          seeded_in: imdb_movies, imdb_persons, tmdb_series
          example_canonical_id: mov_tt4000003
          slot: runtime_minutes
          expected: normalized

        "1.2":
          description: "Null values in required slots → fail validation"
          seeded_in: imdb_movies
          example_canonical_id: mov_tt4000012
          slot: primary_title
          expected: validation_error

        "1.3":
          description: "Empty string vs null"
          seeded_in: imdb_movies
          example_canonical_id: mov_tt4000020
          slot: rating
          seeded_value: "0.0"
          expected: normalized

        "1.4":
          description: "Leading/trailing/internal whitespace"
          seeded_in: imdb_movies
          example_canonical_id: mov_tt4000008
          slot: primary_title
          expected: normalized

        "1.5":
          description: "Mixed case enum values (DIRECTOR, Director, dIRECTOR)"
          seeded_in: imdb_credits, tmdb_credits
          example_canonical_id: cr_tmdb_credits_000020
          slot: role
          seeded_value: "DIRECTOR"
          expected: normalized_to_director

        "1.6":
          description: "Unicode names with diacritics"
          seeded_in: imdb_persons, tmdb_persons, wikidata_persons
          example_canonical_id: per_nm5000022
          slot: name
          seeded_value: "Alejandro González Iñárritu"
          expected: normalized

        "1.7":
          description: "Non-Latin scripts (CJK, Cyrillic, Arabic)"
          seeded_in: imdb_persons, imdb_movies
          example_canonical_ids:
            - per_nm5000030  # CJK name
            - per_nm5000032  # Cyrillic name
            - mov_tt4000024  # CJK original_title
          expected: normalized

        "1.8":
          description: "Date format drift across sources"
          seeded_in: imdb_persons (ISO), tmdb_persons (US), wikidata_persons (EU+named)
          example_canonical_id: per_nm5000010
          slot: birthdate
          seeded_values: ["1943-07-14", "07/14/1943", "14 Jul 1943"]
          expected: normalized_to_iso

        "1.9":
          description: "Numeric edge values (year=0, runtime=0)"
          seeded_in: imdb_movies
          example_canonical_id: mov_tt4000018
          slot: year
          seeded_value: "0"
          expected: normalized

        "1.10":
          description: "Encoding variants (en-dash vs em-dash vs hyphen)"
          seeded_in: wikidata_movies
          example_canonical_id: mov_tt4000025
          slot: primary_title
          seeded_value: "Spider\\u2013Man"
          expected: normalized_to_hyphen

        "1.11":
          description: "Trailing whitespace in CSV cells"
          seeded_in: imdb_movies
          example_canonical_id: mov_tt4000006
          slot: genres
          expected: normalized

        "1.12":
          description: "Same identifier, conflicting payloads"
          seeded_in: imdb_movies, tmdb_movies
          example_canonical_id: mov_tt4000010
          slot: year
          expected: resolved_by_argmax_trust

        "1.13":
          description: "Missing identifier → drop"
          seeded_in: imdb_movies, imdb_persons
          expected: drop

        "1.14":
          description: "Discriminator-routed multi-class sources"
          seeded_in: imdb_series (kind column routes to Movie or Series)
          routed_to_movie: rows where kind=movie
          routed_to_series: rows where kind=series
          expected: correct_class_routing

        "1.15":
          description: "Wildcard-drop rows"
          seeded_in: wikidata_series, rawg_games
          kind_value: exclude
          expected: dropped

        "2.1":
          description: "Known dupes across sources"
          seeded_in: imdb_movies, tmdb_movies, wikidata_movies
          example_canonical_ids: [mov_tt4000000, mov_tt4000001]
          ground_truth: same_entity

        "2.2":
          description: "Known non-dupes"
          seeded_in: imdb_movies, tmdb_movies
          example_canonical_ids: [mov_tt4000020, mov_tt4000021]
          ground_truth: different_entity

        "2.3":
          description: "Three-way dupes"
          seeded_in: imdb_movies, tmdb_movies, wikidata_movies
          example_canonical_id: mov_tt4000005
          ground_truth: same_entity

        "2.4":
          description: "Asymmetric coverage"
          seeded_in: wikidata_movies (absence)
          example_canonical_id: mov_tt4000015
          expected: present_in_two_absent_in_third

        "2.5":
          description: "Surfaced conflicts"
          seeded_in: imdb_movies, tmdb_movies
          slot: year
          expected: surfaced_for_review

        "3.1":
          description: "Full agreement (3 sources same value)"
          example_canonical_id: mov_tt4000000
          slot: primary_title
          expected: degenerate_agree

        "3.2":
          description: "2-vs-1 majority"
          example_canonical_id: mov_tt4000010
          slot: year
          expected: mode_wins

        "3.3":
          description: "3 distinct values → ARGMAX_TRUST tiebreak"
          example_canonical_id: mov_tt4000011
          slot: year
          expected: argmax_trust_tiebreak

        "3.5":
          description: "Source contributes null vs absent"
          example_canonical_id: mov_tt4000003
          slot: runtime_minutes
          expected: null_vs_absent_distinct

        "3.6":
          description: "UNIQUE_OR_FAIL violation"
          example_canonical_id: per_nm5000050
          slot: imdb_id
          expected: UniqueOrFailError

        "3.7":
          description: "MEDIAN_NUMERIC on integer slot"
          example_canonical_id: mov_tt4000000
          slot: runtime_minutes
          expected: median_value

        "3.8":
          description: "LATEST_WATERMARK with backdated asserted_at"
          example_canonical_id: mov_tt4000020
          slot: rating
          expected: latest_watermark_wins

        "3.9":
          description: "WEIGHTED_VOTE with high-trust outlier"
          example_canonical_id: mov_tt4000025
          slot: year
          expected: weighted_vote_resolution

        "3.10":
          description: "Each ResolutionPolicy exercised at least once"
          slots:
            ARGMAX_TRUST: movie.primary_title
            MODE: movie.genres
            MEDIAN_NUMERIC: movie.runtime_minutes
            LATEST_WATERMARK: movie.rating
            UNIQUE_OR_FAIL: person.imdb_id
            WEIGHTED_VOTE: movie.year

        "4.1":
          description: "Credit references existing Movie + Person"
          example_canonical_id: cr_imdb_credits_000000
          expected: fk_resolves

        "4.2":
          description: "Reference through merged canonical_id"
          expected: lineage_redirect

        "4.3":
          description: "Orphan reference → loud failure"
          seeded_in: tmdb_credits
          example_canonical_id: cr_tmdb_credits_000010
          slot: work_id
          expected: OrphanReferenceError

        "4.4":
          description: "Reference through Identifier polymorphic class"
          seeded_in: imdb_identifiers, tmdb_identifiers
          example_identifier_id: id_imdb_identifiers_00000
          expected: polymorphic_fk_resolves

        "4.5":
          description: "Cyclical references → compile error (mutation test)"
          expected: mutation_test_only

        "5.1":
          description: "Empty derivation result"
          example_canonical_id: mov_tt4000029
          derived_slot: director
          expected: empty_list

        "5.2":
          description: "Single-result derivation"
          example_canonical_id: mov_tt4000000
          derived_slot: director
          expected: single_person_canonical_id

        "5.3":
          description: "Multi-result derivation"
          example_canonical_id: mov_tt4000000
          derived_slot: actors
          expected: list_of_person_canonical_ids

        "5.4":
          description: "Deep derivation chain"
          example_path: Episode.parent_series.creator.country.name
          expected: string_country_name

        "5.6":
          description: "Forward-chained at materialization"
          expected: neo4j_DIRECTED_BY_edge_exists

        "6.8":
          description: "Subclass hierarchy edit (Episode adds parent_series) — mutation"
          expected: mutation_test_only

        "8.1":
          description: "Identifier with multiple entity_class values"
          seeded_in: imdb_identifiers
          entity_classes: [Movie, Series, Episode, Game, Person]
          expected: all_classes_represented

        "8.2":
          description: "Polymorphic slot used as ER signal — must be explicitly declared"
          seeded_in: imdb_identifiers, tmdb_identifiers
          expected: cross_references_declared_in_er_credit

        "8.3":
          description: "Subclass query semantics (Title returns Movie+Series+Episode+Game)"
          expected: subclass_query_returns_all_four

        "8.4":
          description: "Override slot in subclass"
          subclass: Episode
          override_slot: parent_series
          expected: not_present_on_Movie_or_Series

        "8.5":
          description: "is_a chain of depth >= 3 (Movie → Title → abstract)"
          path: Movie.is_a=Title
          depth: 1_direct_subclass_of_abstract_Title
          expected: subclass_hierarchy_correct

        "8.6":
          description: "Identifier merged across systems"
          seeded_in: imdb_identifiers, wikidata_identifiers
          expected: cross_system_identifier_merge

        "11.7":
          description: "Multi-class primary — spec.classes variant"
          impl: iceberg_publisher.py
          expected: all_concrete_classes_written

        "11.8":
          description: "DerivedSlot as primary → edge view"
          impl: neo4j_publisher.py
          expected: edge_views_emitted

        "12.8":
          description: "Replay of compile hash produces byte-identical output"
          expected: deterministic_replay

        "13.2":
          description: "POST /runs/full (whole-pipeline trigger)"
          expected: full_toposort_run_all_classes

        "14.2":
          description: "Multi-class materialization"
          impl: neo4j_publisher.py
          expected: all_concrete_nodes_written

        "14.4":
          description: "Derived-edge materialization"
          impl: neo4j_publisher.py
          expected: DIRECTED_BY_edge_exists

        "14.5":
          description: "Materialization of subclass query"
          impl: neo4j_publisher.py
          expected: title_subclass_nodes_carry_Title_label

        "14.6":
          description: "Materialization with Config.exclude_classes"
          impl: neo4j_publisher.py
          expected: excluded_class_absent

        "15.1":
          description: "Audit walk-back: fact → run → compile_hash → spec_revs → source rows"
          expected: full_audit_walk_including_subclass_polymorphic
    """)
    path.write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# C2 generator
# ---------------------------------------------------------------------------

class C2FixtureGenerator:
    """Generator for the C2 rich-ontology stress tier."""

    def __init__(self, tier: TierConfig) -> None:
        self.tier = tier

    def _rng_for(self, cls_name: str, source_name: str) -> random.Random:
        derived = self.tier.seed ^ (_stable_hash(f"{cls_name}:{source_name}") & 0xFFFFFFFF)
        return random.Random(derived)

    def write(self, out_dir: Path) -> None:
        out_dir.mkdir(parents=True, exist_ok=True)

        vols = self.tier.base_volumes
        n_movies  = vols.get("Movie", 30)
        n_series  = vols.get("Series", 10)
        n_episodes = vols.get("Episode", 50)
        n_games   = vols.get("Game", 10)
        n_persons = vols.get("Person", 80)
        n_credits = vols.get("Credit", 100)
        n_ids     = vols.get("Identifier", 50)
        n_studios = vols.get("Studio", 5)
        n_awards  = vols.get("Award", 20)

        first_movie_rows: list[dict] = []
        first_series_rows: list[dict] = []

        # Movie CSVs
        for src in self.tier.sources_per_class.get("Movie", []):
            rng = self._rng_for("Movie", src)
            rows = _c2_movie_rows(n_movies, rng, src)
            _write_c2_generic_csv(out_dir / "sources" / f"{src}.csv", rows)
            if not first_movie_rows:
                first_movie_rows = rows

        # Series CSVs
        for src in self.tier.sources_per_class.get("Series", []):
            rng = self._rng_for("Series", src)
            rows = _c2_series_rows(n_series, rng, src)
            _write_c2_generic_csv(out_dir / "sources" / f"{src}.csv", rows)
            if not first_series_rows:
                first_series_rows = rows

        # Episode CSVs
        for src in self.tier.sources_per_class.get("Episode", []):
            rng = self._rng_for("Episode", src)
            rows = _c2_episode_rows(n_episodes, n_series, rng, src)
            _write_c2_generic_csv(out_dir / "sources" / f"{src}.csv", rows)

        # Game CSVs
        for src in self.tier.sources_per_class.get("Game", []):
            rng = self._rng_for("Game", src)
            rows = _c2_game_rows(n_games, rng, src)
            _write_c2_generic_csv(out_dir / "sources" / f"{src}.csv", rows)

        # Person CSVs
        for src in self.tier.sources_per_class.get("Person", []):
            rng = self._rng_for("Person", src)
            rows = _c2_person_rows(n_persons, rng, src)
            _write_b2_person_csv(out_dir / "sources" / f"{src}.csv", rows)

        # Credit CSVs
        for src in self.tier.sources_per_class.get("Credit", []):
            rng = self._rng_for("Credit", src)
            rows = _c2_credit_rows(n_credits, n_movies, n_series, n_persons, rng, src)
            _write_c2_generic_csv(out_dir / "sources" / f"{src}.csv", rows)

        # Identifier CSVs
        for src in self.tier.sources_per_class.get("Identifier", []):
            rng = self._rng_for("Identifier", src)
            rows = _c2_identifier_rows(n_ids, rng, src)
            _write_c2_generic_csv(out_dir / "sources" / f"{src}.csv", rows)

        # Studio CSVs
        for src in self.tier.sources_per_class.get("Studio", []):
            rng = self._rng_for("Studio", src)
            rows = _c2_studio_rows(n_studios, rng, src)
            _write_c2_generic_csv(out_dir / "sources" / f"{src}.csv", rows)

        # Award CSVs
        for src in self.tier.sources_per_class.get("Award", []):
            rng = self._rng_for("Award", src)
            rows = _c2_award_rows(n_awards, n_persons, rng, src)
            _write_c2_generic_csv(out_dir / "sources" / f"{src}.csv", rows)

        # Country CSVs
        for src in self.tier.sources_per_class.get("Country", []):
            rows = _c2_country_rows(_C2_COUNTRY_CODES)
            _write_c2_generic_csv(out_dir / "sources" / f"{src}.csv", rows)

        # expected_facts.yaml
        _c2_expected_facts(out_dir / "expected_facts.yaml", first_movie_rows, first_series_rows)

        # edge_cases.yaml
        _c2_edge_cases(out_dir / "edge_cases.yaml")

        # Preserve hand-authored spec.py and impls
        if not (out_dir / "spec.py").exists():
            (out_dir / "spec.py").write_text(
                "# Hand-authored — see C2/spec.py\n", encoding="utf-8"
            )


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
        cfg = getattr(importlib.import_module("tools.tier_configs"), args.tier)
    except AttributeError:
        print(f"Unknown tier: {args.tier}", file=sys.stderr)
        sys.exit(1)

    out = Path(__file__).parent / args.tier

    if args.tier == "A1":
        gen = FixtureGenerator(cfg)
    elif args.tier == "B2":
        gen = B2FixtureGenerator(cfg)
    elif args.tier == "C2":
        gen = C2FixtureGenerator(cfg)
    else:
        gen = FixtureGenerator(cfg)

    gen.write(out)
    print(f"Generated {args.tier} → {out}")
