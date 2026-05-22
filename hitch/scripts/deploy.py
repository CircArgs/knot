"""Initial schema + weight seed. Idempotent — re-run anytime.

Run inside the hitch-worker container (or anywhere with HITCH_PG_DSN
pointed at the live postgres).
"""

from __future__ import annotations

import logging

from hitch import config
from hitch.db import connect
from hitch.spec import build_spec

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("hitch.deploy")

# (source, class, slot) → weight policy. _user_corrections dominates
# the resolver argmax; the declared sources rank imdb > tmdb > rt.
_DEFAULT_WEIGHTS = {
    "imdb": 0.85,
    "tmdb": 0.70,
    "rottentomatoes": 0.50,
    "_user_corrections": 1e6,
}


def main() -> None:
    cfg = config.load()
    spec = build_spec(schema=cfg.pg_schema, embedding_dim=cfg.embedding_dim)
    log.info(
        "applying spec to schema=%s embedding_dim=%d", cfg.pg_schema, cfg.embedding_dim
    )

    with connect(cfg.pg_dsn) as conn, conn.cursor() as cur:
        # psycopg3 supports multi-statement scripts in one execute().
        cur.execute(spec.ddl())
        log.info("schema applied")

        # Weight rows — INSERT-only via binding.upsert_weight_sql.
        for binding in spec.source_bindings:
            weight = _DEFAULT_WEIGHTS.get(binding.source.name, 0.0)
            upsert = binding.upsert_weight_sql()
            ident = binding.identifier_slot.name
            for slot in binding.class_.effective_slots():
                if slot.name == ident:
                    continue
                cur.execute(upsert, {"slot_name": slot.name, "weight": weight})
        log.info("weights seeded")


if __name__ == "__main__":
    main()
