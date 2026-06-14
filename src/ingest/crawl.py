"""Keyword-based Weibo scraper using crawl4weibo library as fallback.

NOTE: crawl4weibo requires Playwright + Chromium installed in the container.
To enable deep scraping, rebuild the pipeline with the chromium-bundled base image
or install playwright browsers: `playwright install chromium`
"""

from __future__ import annotations

import asyncio
import datetime
import hashlib

import structlog

from src.config import Config
from src.store.models import Database, Post

logger = structlog.get_logger()

# Track whether we've already warned about missing browser to avoid log spam
_crawl4weibo_available: bool | None = None


def _check_crawl4weibo() -> bool:
    """Check if crawl4weibo is usable. Returns False if missing or browser not installed."""
    global _crawl4weibo_available
    if _crawl4weibo_available is not None:
        return _crawl4weibo_available

    try:
        from crawl4weibo import WeiboClient

        # Quick init test — will fail if Playwright browser is missing
        client = WeiboClient()
        _crawl4weibo_available = True
        client.close()
        return True
    except ImportError:
        logger.warning("crawl4weibo.package_not_installed")
        _crawl4weibo_available = False
        return False
    except Exception as e:
        msg = str(e)
        if "Executable doesn't exist" in msg or "Playwright browser" in msg:
            logger.warning(
                "crawl4weibo.browser_missing",
                hint="Run 'playwright install chromium' in the pipeline container",
            )
        else:
            logger.warning("crawl4weibo.init_failed", error=msg)
        _crawl4weibo_available = False
        return False


class CrawlIngestor:
    """Performs keyword searches on Weibo using crawl4weibo for deeper coverage.

    This catches content from accounts not in the monitored list —
    community posts, breaking news from non-followed sources, etc.

    Falls back silently if crawl4weibo browser is not available.
    """

    def __init__(self, config: Config) -> None:
        self.config = config

    async def _search_keyword(self, keyword: str, max_posts: int = 25) -> list[dict]:
        """Search Weibo for a keyword using crawl4weibo."""
        try:
            from crawl4weibo import WeiboClient

            loop = asyncio.get_running_loop()

            def _search():
                client = WeiboClient()
                try:
                    return client.search_posts(keyword, limit=max_posts)
                finally:
                    client.close()

            results = await loop.run_in_executor(None, _search)

            posts = []
            for item in results:
                if not item.get("text"):
                    continue

                posts.append({
                    "weibo_id": str(item.get("id", hashlib.md5(item["text"].encode()).hexdigest())),
                    "author_name": item.get("user", {}).get("screen_name", "unknown"),
                    "author_uid": str(item.get("user", {}).get("id", "")),
                    "content": item["text"],
                    "url": item.get("scheme", f"https://weibo.com/{item.get('user', {}).get('id', '')}/{item.get('id', '')}"),
                    "published_at": item.get("created_at", datetime.datetime.utcnow()),
                })

            return posts

        except Exception as e:
            logger.debug("crawl4weibo.search_skipped", keyword=keyword, reason=str(e)[:80])
            return []

    async def ingest(self, db: Database) -> int:
        """Run keyword searches and ingest new posts. Returns count of new posts."""
        if not _check_crawl4weibo():
            return 0

        new_count = 0
        high_priority_terms = []

        for group in self.config.keyword_groups:
            if group.priority == "high":
                high_priority_terms.extend(group.terms)

        # Search high-priority terms
        for term in high_priority_terms:
            logger.debug("crawl.searching", term=term)
            results = await self._search_keyword(
                term, max_posts=self.config.deep_scrape.max_posts_per_search
            )

            for raw in results:
                post = Post(
                    weibo_id=raw["weibo_id"],
                    source="crawl",
                    author_name=raw["author_name"],
                    author_uid=raw.get("author_uid"),
                    content=raw["content"],
                    content_hash=Post.compute_hash(raw["content"]),
                    url=raw["url"],
                    published_at=raw["published_at"]
                    if isinstance(raw["published_at"], datetime.datetime)
                    else datetime.datetime.utcnow(),
                )

                inserted = await db.insert_post(post)
                if inserted:
                    new_count += 1

            # Rate-limit: pause between keyword searches
            await asyncio.sleep(2.0)

        logger.info("crawl.ingest_complete", terms_searched=len(high_priority_terms), new_posts=new_count)
        return new_count
