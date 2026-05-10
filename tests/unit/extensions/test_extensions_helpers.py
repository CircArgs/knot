"""Pure-Python tests for the extensions package.

Currently covers the ER ``register`` decorator's duplicate-source guard.
Dispatcher priority / isinstance / no-op tests use ``pg_conn`` to build
a ``RequestContext`` and live in
``tests/integration/extensions/test_extensions.py`` alongside the live
end-to-end ingest test.
"""

from __future__ import annotations

import pytest


def test_er_register_duplicate_raises():
    from knot.extensions import er as er_module

    name = "_dup_test_source"
    er_module._RESOLVERS[name] = lambda rows, source: []
    try:
        with pytest.raises(ValueError, match="already registered"):

            @er_module.register(name)
            def _fn(rows, source):
                return []
    finally:
        del er_module._RESOLVERS[name]
