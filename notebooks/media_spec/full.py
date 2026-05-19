# isort: skip_file
"""Compose the migration-time domains onto the v1 spec.

Loading ``spec`` gives you the v1 (base + person + movies).
Loading THIS module includes the four additional domains —
``games`` / ``podcasts`` / ``tv`` / ``webscraped`` — by calling
``spec.include(domain.part)`` on each.

Same shape as a FastAPI app loading more routers at startup.

Order matters because tv + webscraped both reference movies'
source/class handles (tmdb, imdb, Movie). movies has already been
included in the v1 by ``__init__.py`` — so by the time tv.py or
webscraped.py runs, those handles point at the parent spec.

``# isort: skip_file`` keeps the hand-ordered narrative.
"""

import media_spec  # noqa: F401  — ensures v1 has loaded first
from media_spec.base import spec

from media_spec import games as _games
from media_spec import podcasts as _podcasts
from media_spec import tv as _tv
from media_spec import webscraped as _webscraped

spec.include(_games.part)
spec.include(_podcasts.part)
spec.include(_tv.part)
spec.include(_webscraped.part)

# optional explicit validate. # .ddl validates internally before returning
spec.validate()
