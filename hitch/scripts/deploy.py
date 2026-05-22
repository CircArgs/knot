"""Initial schema + weight seed. Idempotent — re-run anytime.

Run inside the hitch-worker container (or anywhere with HITCH_PG_DSN
pointed at the live postgres).
"""

from __future__ import annotations

import logging

from knot.compile import emit_weight_seed

from hitch import config
from hitch.db import connect
from hitch.spec import build_spec

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("hitch.deploy")

# Source-rank defaults — operators tune these per (class, slot) at
# runtime via the GraphQL mutation surface or direct
# `binding.upsert_weight_sql()`. _user_corrections is intentionally
# absent here: `emit_weight_seed` auto-seeds it at
# CORRECTIONS_DEFAULT_WEIGHT (1e6) so a host that forgets to set it
# can't silently weight corrections at 0.
_DEFAULT_WEIGHTS = {
    "imdb": 0.85,
    "tmdb": 0.70,
    "rottentomatoes": 0.50,
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

        # Weight seed — INSERT-only (ON CONFLICT DO NOTHING). Preserves
        # operator tuning across re-deploys. Auto-includes the
        # _user_corrections row at the safe default so the corrections
        # mutation surface never produces silently-lost writes.
        seeded = 0
        for sql, params in emit_weight_seed(spec, defaults=_DEFAULT_WEIGHTS):
            cur.execute(sql, params)
            seeded += 1
        log.info("weights seeded (%d rows; ON CONFLICT DO NOTHING)", seeded)


if __name__ == "__main__":
    main()
