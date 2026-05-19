"""The shared ``spec`` object — created here, then assembled from
the domain modules.

Same shape as a FastAPI ``main.py``: this file owns the top-level
object (``spec``), each domain owns its own collector (``part``,
analogous to ``APIRouter``), and we ``spec.include(part)`` them in.

``__init__.py`` decides which domains are loaded by default — the
v1 surface (``person`` + ``movies``). ``full.py`` opts the rest in
(``games`` / ``podcasts`` / ``tv`` / ``webscraped``), and that
import IS the migration story.
"""

# Same shape as FastAPI's main.py — one top-level spec; each domain
# owns a "part" (the APIRouter analogue) composed in via spec.include().
from _demo import SCHEMA

from knot import Spec

spec = Spec(identifier_slot_name="canonical_id", schema=SCHEMA)
