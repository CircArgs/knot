#!/usr/bin/env bash
set -euo pipefail

TIMEOUT=60
INTERVAL=2
elapsed=0

services=(knot-test-postgres knot-test-neo4j)

echo "Waiting for services to become healthy (timeout: ${TIMEOUT}s)..."

while true; do
    all_healthy=true
    for name in "${services[@]}"; do
        status=$(docker inspect --format '{{.State.Health.Status}}' "$name" 2>/dev/null || echo "missing")
        if [[ "$status" != "healthy" ]]; then
            all_healthy=false
            echo "  $name: $status"
        fi
    done

    if $all_healthy; then
        echo "All services healthy after ${elapsed}s."
        exit 0
    fi

    if (( elapsed >= TIMEOUT )); then
        echo "ERROR: Timed out after ${TIMEOUT}s waiting for services to become healthy." >&2
        for name in "${services[@]}"; do
            status=$(docker inspect --format '{{.State.Health.Status}}' "$name" 2>/dev/null || echo "missing")
            echo "  $name: $status" >&2
        done
        exit 1
    fi

    sleep "$INTERVAL"
    elapsed=$(( elapsed + INTERVAL ))
done
