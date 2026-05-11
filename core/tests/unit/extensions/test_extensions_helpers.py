"""Pure-Python tests for the extensions package.

Currently covers the ER module's KNOT_ER_URL behavior. The handler is
only registered on the master dispatcher when KNOT_ER_URL is set; we
verify that with a fresh reload of the module under each env-var
condition.

Dispatcher priority / isinstance / no-op tests use ``pg_conn`` to
build a ``RequestContext`` and live in
``tests/integration/extensions/test_extensions.py``.
"""

from __future__ import annotations

import importlib


def _reload_er_clean(er_module) -> None:
    """Reload knot.extensions.er with a clean module namespace.

    importlib.reload re-executes the module body but keeps existing
    attributes that the body doesn't reassign. We drop `_delegate`
    explicitly so the post-reload module state reflects the current env.
    """
    er_module.__dict__.pop("_delegate", None)
    importlib.reload(er_module)


def test_er_module_no_handler_when_url_unset(monkeypatch):
    """Without KNOT_ER_URL, importing knot.extensions.er registers no
    handler on the master dispatcher.
    """
    monkeypatch.delenv("KNOT_ER_URL", raising=False)

    from knot.extensions import dispatch as _dispatch
    from knot.extensions import er as er_module

    handlers_before = list(_dispatch._handlers)
    try:
        _reload_er_clean(er_module)
        assert not hasattr(er_module, "_delegate")
    finally:
        _dispatch._handlers[:] = handlers_before


def test_er_module_registers_handler_when_url_set(monkeypatch):
    """With KNOT_ER_URL set, the module exposes `_delegate` and the
    handler shows up on the dispatcher. Restore on exit so the leaked
    handler doesn't fire in later tests.
    """
    monkeypatch.setenv("KNOT_ER_URL", "http://er.test")

    from knot.extensions import dispatch as _dispatch
    from knot.extensions import er as er_module

    handlers_before = list(_dispatch._handlers)
    try:
        _reload_er_clean(er_module)
        assert hasattr(er_module, "_delegate")
        registered = [fn for _et, _pri, fn in _dispatch._handlers]
        assert er_module._delegate in registered
    finally:
        monkeypatch.delenv("KNOT_ER_URL", raising=False)
        _reload_er_clean(er_module)
        _dispatch._handlers[:] = handlers_before
