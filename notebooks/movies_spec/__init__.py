"""Shared spec for the walkthrough notebooks — composable.

The package is the canonical example of how a real team file-splits
a knot spec:

  base.py       Core entities + the foundational source binding.
                Always loaded; this is the "v1" the demo starts from.

  full.py       Uber-complete extensions: a second source (tmdb) and
                the title_embedding VECTOR(384) slot used for ER.
                Opt-in — *importing* this module mutates the shared
                ``spec`` object, which is how a team grows a spec
                over time. The migration notebook is the place this
                import happens.

A real deployment splits even finer (one file per source, one per
extension); the package shape is the pattern. ``__init__.py`` exposes
only the base — extensions stay explicit so the notebook narrative
shows when they get composed in.
"""

from movies_spec.base import (
    imdb,
    imdb_movie_b,
    imdb_person_b,
    movie,
    person,
    spec,
)
