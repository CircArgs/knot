FROM python:3.12-slim

# System deps for psycopg-binary + sentence-transformers (libstdc++)
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Knot + knot_graphql + hitch are installed editable so changes on the
# host reflect inside the container during dev (volumes mount the trees).
COPY pyproject.toml /tmp/knot-pyproject.toml
COPY hitch/pyproject.toml /tmp/hitch-pyproject.toml

# Pre-install heavy deps (sentence-transformers, torch) for cache reuse.
RUN pip install --no-cache-dir \
        "psycopg[binary]>=3.1" \
        "pgvector>=0.3" \
        "ariadne>=0.23" \
        "fastapi>=0.115" \
        "uvicorn[standard]>=0.32" \
        "temporalio>=1.7" \
        "sentence-transformers>=2.7" \
        "requests>=2.32"

# Pre-download the embedding model so the worker doesn't pay for it on
# first run. Pin the model name so cache hits.
ENV HITCH_EMBEDDING_MODEL=sentence-transformers/all-MiniLM-L6-v2
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('${HITCH_EMBEDDING_MODEL}')"

# Source mounts cover /app/knot, /app/knot_graphql, /app/hitch at runtime
# (see compose.yml). Editable install is wired during the entrypoint.
COPY <<'EOF' /app/entrypoint.sh
#!/bin/sh
set -e
# Project root (knot package + knot_graphql package, both via the same pyproject)
# is mounted at /app/repo. hitch lives inside it at /app/repo/hitch.
pip install --no-deps --quiet -e /app/repo -e /app/repo/hitch
cd /app/repo/hitch
exec "$@"
EOF
RUN chmod +x /app/entrypoint.sh

ENTRYPOINT ["/app/entrypoint.sh"]
