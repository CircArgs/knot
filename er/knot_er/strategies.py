"""Resolution strategies. identifier_passthrough is the trivial default.

Real strategies (fuzzy, ML matchers, embedding similarity) land later
as sibling functions. Selection between strategies will be config-driven."""


def identifier_passthrough(rows: list[dict], identifier_slot: str) -> list[str]:
    return [str(r[identifier_slot]) for r in rows]


# TODO: fuzzy_match, ml_match, embedding_match
