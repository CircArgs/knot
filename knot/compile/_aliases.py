"""JOIN alias derivation for FK chain reads.

A stable, deterministic alias for each step of an FK chain so that
two FK slots pointing at the same target class produce distinct JOIN
aliases — e.g. ``movie_director`` vs ``movie_writer`` — while the
same chain referenced multiple times in one query still produces a
single JOIN.

Both ``knot.compile.query`` (JOIN emission) and ``knot.compile.expr``
(``FkChainRef`` rendering) call ``chain_alias`` with the same arguments,
so alias assignment is pure and consistent without passing a map around.
"""

from __future__ import annotations


def chain_alias(source_class: str, chain: tuple[tuple[str, str], ...]) -> str:
    """Return the SQL alias for the JOIN introduced by ``chain``.

    The alias is derived purely from the FK path — source class name
    followed by each FK slot name in the chain, joined with underscores.
    The target class name is intentionally excluded so that two different
    FK slots pointing at the same target get distinct aliases.

    Examples::

        chain_alias("Movie", (("director", "Person"),))
        # → "movie_director"

        chain_alias("Movie", (("writer", "Person"),))
        # → "movie_writer"

        chain_alias("Movie", (("director", "Person"), ("employer", "Company")))
        # → "movie_director_employer"
    """
    parts = [source_class.lower()] + [fk_slot for fk_slot, _ in chain]
    return "_".join(parts)
