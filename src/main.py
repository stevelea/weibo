# Weibo → evconduit.com Pipeline
# Main entry point — scheduler loop
from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
import time
from pathlib import Path

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

            # Phase 1c: Ingest from Bilibili (B站)
            bl_count = 0
            for bl in self.config.bilibili_accounts:
                bl_count += await self.rsshub.ingest_bilibili(self.db, bl.uid, bl.name)
            logger.info("cycle.bilibili_done", posts_ingested=bl_count)

            # Phase 1d: Ingest from Zhihu (知乎日报)
            zh_count = await self.rsshub.ingest_zhihu_daily(self.db)
            logger.info("cycle.zhihu_done", posts_ingested=zh_count)

            # Phase 1e: Ingest from external RSS feeds (ChinaPEV, CarNewsChina, etc.)
            fd_count = 0
            for fd in self.config.external_feeds:
                fd_count += await self.rsshub.ingest_native_feed(
                    self.db, fd["url"], fd["name"]
                )
            logger.info("cycle.feeds_done", posts_ingested=fd_count)

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

            # Phase 6: Cleanup posts older than 90 days
            old_posts = await self.db.cleanup_old_posts(days=90)
            if old_posts:
                for old in old_posts:
                    slug = self.publisher._slugify(old.title_en or "untitled", old.weibo_id)
                    # Delete markdown file
                    md_path = self.publisher.posts_dir / f"{slug}.md"
                    md_path.unlink(missing_ok=True)
                    # Delete local images
                    if old.image_local_paths:
                        try:
                            paths = json.loads(old.image_local_paths)
                            for p in paths:
                                if p:
                                    (Path("/output/static") / p.lstrip("/")).unlink(missing_ok=True)
                        except Exception:
                            pass
                logger.info("cycle.cleanup_done", posts_deleted=len(old_posts))

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
