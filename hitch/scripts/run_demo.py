"""End-to-end demo runner — fires every workflow and prints a report.

Sequence:
  1. Per (source, class): IngestWorkflow
  2. Per (source, Movie, title_embedding): EmbedWorkflow
  3. Per class: ERWorkflow (Person first — Movie's director FKs cascade)
  4. ValidationSweepWorkflow
  5. Query the resolved view via the API URL the user provides
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import uuid

from temporalio.client import Client

from hitch import config
from hitch.workflows import (
    EmbedWorkflow,
    ERWorkflow,
    IngestWorkflow,
    ValidationSweepWorkflow,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("hitch.demo")


async def amain() -> None:
    cfg = config.load()
    log.info("connecting to temporal @ %s", cfg.temporal_address)
    client = await Client.connect(
        cfg.temporal_address, namespace=cfg.temporal_namespace
    )

    # Order matters: ER Persons + Studios before Movies + Credits so
    # the backward-fanout from those Person / Studio stamps translates
    # Movie.director / Movie.studio / Credit.person FK columns from
    # source-ids → canonical-ids before ER even reaches Movie/Credit.
    classes = ["Person", "Studio", "Movie", "Credit"]
    sources = ["imdb", "tmdb", "rottentomatoes"]

    # 1. Ingest
    log.info("=== ingest ===")
    for source_name in sources:
        for cls_name in classes:
            wid = f"ingest-{source_name}-{cls_name}-{uuid.uuid4().hex[:8]}"
            result = await client.execute_workflow(
                IngestWorkflow.run,
                args=[source_name, cls_name],
                id=wid,
                task_queue=cfg.task_queue,
            )
            log.info("  %s/%s → %s", source_name, cls_name, result)

    # 2. Embeddings
    log.info("=== embeddings (Movie.title_embedding) ===")
    for source_name in sources:
        wid = f"embed-{source_name}-{uuid.uuid4().hex[:8]}"
        result = await client.execute_workflow(
            EmbedWorkflow.run,
            args=[source_name, "Movie", "title_embedding", 32],
            id=wid,
            task_queue=cfg.task_queue,
        )
        log.info("  %s → %s", source_name, result)

    # 3. ER — order matters because of FK fan-out
    log.info("=== ER ===")
    for cls_name in classes:
        wid = f"er-{cls_name}-{uuid.uuid4().hex[:8]}"
        result = await client.execute_workflow(
            ERWorkflow.run,
            args=[sources, cls_name],
            id=wid,
            task_queue=cfg.task_queue,
        )
        log.info("  %s → %s", cls_name, result)

    # 4. Validation
    log.info("=== validation sweep ===")
    wid = f"validation-{uuid.uuid4().hex[:8]}"
    report = await client.execute_workflow(
        ValidationSweepWorkflow.run,
        id=wid,
        task_queue=cfg.task_queue,
    )
    print(json.dumps(report, indent=2, default=str))


def main() -> None:
    try:
        asyncio.run(amain())
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
