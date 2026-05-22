"""Temporal worker entrypoint.

Run with `python -m hitch.worker`. Inside docker compose the
hitch-worker service launches this.
"""

from __future__ import annotations

import asyncio
import logging

from temporalio.client import Client
from temporalio.worker import Worker

from hitch import config
from hitch.workflows import (
    EmbedWorkflow,
    ERWorkflow,
    IngestWorkflow,
    ValidationSweepWorkflow,
)
from hitch.workflows.activities import ALL as ALL_ACTIVITIES

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
log = logging.getLogger("hitch.worker")


async def amain() -> None:
    cfg = config.load()
    log.info(
        "connecting to temporal @ %s ns=%s",
        cfg.temporal_address,
        cfg.temporal_namespace,
    )
    client = await Client.connect(
        cfg.temporal_address, namespace=cfg.temporal_namespace
    )

    worker = Worker(
        client,
        task_queue=cfg.task_queue,
        workflows=[
            IngestWorkflow,
            EmbedWorkflow,
            ERWorkflow,
            ValidationSweepWorkflow,
        ],
        activities=ALL_ACTIVITIES,
    )
    log.info("worker listening on task queue %s", cfg.task_queue)
    await worker.run()


def main() -> None:
    asyncio.run(amain())


if __name__ == "__main__":
    main()
