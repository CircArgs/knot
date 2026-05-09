"""Spec-layer exception classes — published surface for draft lifecycle errors.

These belong on the spec layer because they describe spec-domain
conditions (publish gate failure, draft not found, draft already
published) and are raised from both the orchestration layer
(``knot.graph.spec``) and the persistence layer (``knot.db.spec_store``).
Hosting them here keeps the dependency direction clean: db imports
from spec, graph imports from spec, neither imports the other.
"""

from __future__ import annotations


class PublishGateError(Exception):
    """Raised when the publish gate rejects a candidate spec."""


class DraftNotFoundError(Exception):
    """Raised when a draft revision number doesn't exist."""


class DraftAlreadyPublishedError(Exception):
    """Raised when attempting to mutate a revision that's already published."""
