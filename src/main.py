# Weibo → evconduit.com Pipeline
# Main entry point — scheduler loop
from __future__ import annotations

import asyncio
import os
import signal
import sys
import time

import schedule
import structlog

from src.config import load_config
from src.ingest.crawl import CrawlIngestor
from src.ingest.rsshub import RSSHubIngestor
from src.process.ai import AIProcessor
from src.process.ocr import ImageCaptioner
from src.publish.hugo import HugoPublisher
from src.store.models import Database

logger = structlog.get_logger()


class Pipeline:
    """Orchestrates the full ingestion → process → publish cycle."""

    def __init__(self) -> None:
        self.config = load_config()
        self.db = Database(os.environ["DATABASE_URL"])
        self.rsshub = RSSHubIngestor(self.config)
        self.crawler = CrawlIngestor(self.config)
        self.ai = AIProcessor()
        self.ocr = ImageCaptioner()
        self.publisher = HugoPublisher()

    async def start(self) -> None:
        """Initialize database, Hugo site config, and run one cycle immediately."""
        await self.db.init()
        await self.publisher.ensure_site_config()
        logger.info("pipeline.initialized", db=self.db.url)
        await self.run_cycle()

    async def run_cycle(self) -> None:
        """One complete cycle: ingest → process → publish."""
        cycle_start = time.monotonic()
        logger.info("cycle.started")

        try:
            # Phase 1: Ingest from RSSHub (user timelines)
            rss_count = await self.rsshub.ingest(self.db)
            logger.info("cycle.rsshub_done", posts_ingested=rss_count)

            # Phase 1b: Ingest from super topics (超话)
            st_count = 0
            for st in self.config.supertopics:
                st_count += await self.rsshub.ingest_supertopic(self.db, st["id"], st["name"])
            logger.info("cycle.supertopic_done", posts_ingested=st_count)

            # Phase 2: Ingest from crawl4weibo (keyword search)
            crawl_count = await self.crawler.ingest(self.db)
            logger.info("cycle.crawl_done", posts_ingested=crawl_count)

            # Phase 3: AI processing
            ai_count = await self.ai.process_batch(self.db, limit=20)
            logger.info("cycle.ai_done", posts_processed=ai_count)

            # Phase 4: OCR image captions (saves images to Hugo static dir)
            ocr_count = await self.ocr.process_batch(self.db, limit=20, static_dir="/output/static")
            logger.info("cycle.ocr_done", posts_captioned=ocr_count)

            # Phase 5: Publish to Hugo
            pub_count = await self.publisher.publish(self.db)
            logger.info("cycle.publish_done", posts_published=pub_count)

        except Exception:
            logger.exception("cycle.failed")
            raise

        elapsed = time.monotonic() - cycle_start
        logger.info("cycle.completed", elapsed_seconds=round(elapsed, 1))

    async def shutdown(self) -> None:
        """Clean shutdown."""
        await self.db.close()
        logger.info("pipeline.shutdown")


def main() -> None:
    """Run the pipeline on a schedule."""
    pipeline = Pipeline()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    async def _start():
        await pipeline.start()

    loop.run_until_complete(_start())

    # Register signal handlers for graceful shutdown
    def _shutdown(signum, frame):
        logger.info("signal.received", signal=signum)
        loop.run_until_complete(pipeline.shutdown())
        sys.exit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    # Schedule the cycle
    interval = int(os.environ.get("CYCLE_INTERVAL_MINUTES", "10"))
    schedule.every(interval).minutes.do(
        lambda: loop.run_until_complete(pipeline.run_cycle())
    )

    logger.info("scheduler.started", interval_minutes=interval)

    while True:
        schedule.run_pending()
        time.sleep(30)


if __name__ == "__main__":
    main()
