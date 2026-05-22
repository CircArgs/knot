"""hitch config — env-driven (hitch is an app, not the library).

knot itself bans env-var indirection, but hitch is the host: env vars
are exactly the right place for deploy-shaped knobs (db URL, temporal
address, embedding model).
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Config:
    pg_dsn: str
    pg_schema: str
    temporal_address: str
    temporal_namespace: str
    task_queue: str
    embedding_model: str
    embedding_dim: int
    api_host: str
    api_port: int


def load() -> Config:
    return Config(
        pg_dsn=os.environ.get(
            "HITCH_PG_DSN",
            "postgresql://hitch:hitch@postgres:5432/hitch",
        ),
        pg_schema=os.environ.get("HITCH_PG_SCHEMA", "hitch"),
        temporal_address=os.environ.get("HITCH_TEMPORAL_ADDRESS", "temporal:7233"),
        temporal_namespace=os.environ.get("HITCH_TEMPORAL_NAMESPACE", "default"),
        task_queue=os.environ.get("HITCH_TASK_QUEUE", "hitch-main"),
        embedding_model=os.environ.get(
            "HITCH_EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2"
        ),
        embedding_dim=int(os.environ.get("HITCH_EMBEDDING_DIM", "384")),
        api_host=os.environ.get("HITCH_API_HOST", "0.0.0.0"),
        api_port=int(os.environ.get("HITCH_API_PORT", "8000")),
    )
