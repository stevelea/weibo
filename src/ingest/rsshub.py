"""RSSHub feed consumer — polls Weibo user timelines via RSSHub."""

from __future__ import annotations

import datetime
import re
from typing import Any

import feedparser
import httpx
import structlog

from src.config import Config
from src.store.models import Database, Post

logger = structlog.get_logger()

# Regex to extract Weibo post ID from RSSHub-generated URLs/IDs
WEIBO_ID_RE = re.compile(r"/(\d{16,})$")


class RSSHubIngestor:
    """Polls RSSHub for Weibo user timeline feeds and ingests new posts."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0),
            headers={"User-Agent": "Mozilla/5.0 (compatible; evconduit/1.0; +https://www.evconduit.com)"},
        )

    def _extract_weibo_id(self, url: str, entry_id: str) -> str | None:
        """Extract a stable Weibo post ID from URL, entry ID, or guid.

        RSSHub uses different URL formats:
        - Numeric: https://weibo.com/5710264970/5309360914497603
        - Short:   https://weibo.com/5710264970/R3KAzFnPh

        Falls back to the guid (entry_id) when no numeric match is found.
        """
        # Try numeric ID first (16+ digit number at end of URL)
        for candidate in [url, entry_id]:
            m = WEIBO_ID_RE.search(candidate)
            if m:
                return m.group(1)

        # Fall back to the RSS guid — stable and unique per post
        if entry_id:
            return entry_id

        return None

    async def _fetch_feed(self, uid: str, account_name: str) -> list[dict[str, Any]]:
        """Fetch the RSS feed for a single Weibo user."""
        url = f"{self.config.rsshub_base_url}/weibo/user/{uid}"
        try:
            resp = await self.client.get(url)
            resp.raise_for_status()
        except httpx.HTTPError as e:
            logger.warning("rsshub.fetch_failed", account=account_name, uid=uid, error=str(e))
            return []

        feed = feedparser.parse(resp.text)
        if feed.bozo and not feed.entries:
            logger.warning("rsshub.parse_error", account=account_name, uid=uid, exc=feed.bozo_exception)
            return []

        posts = []
        for entry in feed.entries:
            post_id = self._extract_weibo_id(entry.get("link", ""), entry.get("id", ""))
            if not post_id:
                continue

            # Parse published date
            published = None
            if hasattr(entry, "published_parsed") and entry.published_parsed:
                published = datetime.datetime(*entry.published_parsed[:6])
            else:
                published = datetime.datetime.utcnow()

            # Extract images and clean HTML from content
            raw_html = entry.get("summary", entry.get("description", ""))
            content = re.sub(r"<[^>]+>", "", raw_html).strip()

            # Extract image URLs from the raw HTML
            image_urls = re.findall(r'<img[^>]+src="(https?://[^"]+)"', raw_html)
            # Deduplicate while preserving order
            seen = set()
            unique_images = []
            for img in image_urls:
                if img not in seen:
                    seen.add(img)
                    unique_images.append(img)

            # Extract video source URLs from the raw HTML
            video_urls = re.findall(r'<source[^>]+src="(https?://[^"]+\.mp4[^"]*)"', raw_html)
            seen_v = set()
            unique_videos = []
            for vid in video_urls:
                if vid not in seen_v:
                    seen_v.add(vid)
                    unique_videos.append(vid)

            # Extract Weibo video page URLs (stable, don't expire)
            vp_urls = re.findall(r'video\.weibo\.com/show\?fid=\d+:\d+', raw_html)
            seen_vp = set()
            unique_vp = []
            for vp in vp_urls:
                if vp not in seen_vp:
                    seen_vp.add(vp)
                    unique_vp.append(f"https://{vp}")

            # Extract video poster/thumbnail images
            posters = re.findall(r'poster="(https?://[^"]+)"', raw_html)
            seen_p = set()
            unique_posters = []
            for p in posters:
                if p not in seen_p:
                    seen_p.add(p)
                    unique_posters.append(p)

            if not content:
                continue

            posts.append({
                "weibo_id": post_id,
                "author_name": account_name,
                "author_uid": uid,
                "content": content,
                "image_urls": unique_images,
                "video_urls": unique_videos,
                "video_page_urls": unique_vp,
                "video_posters": unique_posters,
                "url": entry.get("link", f"https://weibo.com/{uid}/{post_id}"),
                "published_at": published,
            })

        logger.debug("rsshub.feed_fetched", account=account_name, post_count=len(posts))
        return posts

    async def ingest(self, db: Database) -> int:
        """Poll all configured accounts and ingest new posts. Returns count of new posts."""
        new_count = 0

        for account in self.config.accounts:
            posts = await self._fetch_feed(account.uid, account.name)

            for raw in posts:
                import json as _json
                image_urls_json = _json.dumps(raw.get("image_urls", [])) if raw.get("image_urls") else None
                video_urls_json = _json.dumps(raw.get("video_urls", [])) if raw.get("video_urls") else None
                video_page_urls_json = _json.dumps(raw.get("video_page_urls", [])) if raw.get("video_page_urls") else None
                video_posters_json = _json.dumps(raw.get("video_posters", [])) if raw.get("video_posters") else None
                post = Post(
                    weibo_id=raw["weibo_id"],
                    source="rsshub",
                    author_name=raw["author_name"],
                    author_uid=raw["author_uid"],
                    content=raw["content"],
                    content_hash=Post.compute_hash(raw["content"]),
                    image_urls=image_urls_json,
                    video_urls=video_urls_json,
                    video_page_urls=video_page_urls_json,
                    video_posters=video_posters_json,
                    url=raw["url"],
                    published_at=raw["published_at"],
                )

                inserted = await db.insert_post(post)
                if inserted:
                    new_count += 1

            # Small delay between accounts to be gentle on RSSHub
            await httpx.AsyncClient().aclose()  # no-op, just a visual separator

        logger.info("rsshub.ingest_complete", accounts_checked=len(self.config.accounts), new_posts=new_count)
        return new_count

    async def _fetch_supertopic(self, topic_id: str) -> list[dict[str, Any]]:
        """Fetch the RSS feed for a Weibo super topic (超话)."""
        url = f"{self.config.rsshub_base_url}/weibo/super_index/{topic_id}"
        try:
            resp = await self.client.get(url)
            resp.raise_for_status()
        except httpx.HTTPError as e:
            logger.warning("rsshub.supertopic_failed", topic_id=topic_id, error=str(e))
            return []

        feed = feedparser.parse(resp.text)
        if feed.bozo and not feed.entries:
            logger.warning("rsshub.supertopic_parse_error", topic_id=topic_id)
            return []

        posts = []
        for entry in feed.entries:
            # Super topic entries have different ID format
            post_id = self._extract_weibo_id(entry.get("link", ""), entry.get("id", ""))
            if not post_id:
                continue

            published = datetime.datetime.utcnow()
            if hasattr(entry, "published_parsed") and entry.published_parsed:
                published = datetime.datetime(*entry.published_parsed[:6])

            raw_html = entry.get("summary", entry.get("description", ""))
            content = re.sub(r"<[^>]+>", "", raw_html).strip()

            # Extract author from title (format: "【Author】Content" or similar)
            author_name = entry.get("author", "社区用户")

            # Extract images
            image_urls = re.findall(r'<img[^>]+src="(https?://[^"]+)"', raw_html)
            seen = set()
            unique_images = []
            for img in image_urls:
                if img not in seen:
                    seen.add(img)
                    unique_images.append(img)

            # Extract video page URLs
            vp_urls = re.findall(r'video\.weibo\.com/show\?fid=\d+:\d+', raw_html)
            unique_vp = list({f"https://{v}" for v in vp_urls})

            # Extract video posters
            posters = re.findall(r'poster="(https?://[^"]+)"', raw_html)
            unique_posters = list(set(posters))

            if not content:
                continue

            posts.append({
                "weibo_id": post_id,
                "author_name": author_name,
                "author_uid": "",
                "content": content,
                "image_urls": unique_images,
                "video_urls": [],
                "video_page_urls": unique_vp,
                "video_posters": unique_posters,
                "url": entry.get("link", f"https://weibo.com/p/{topic_id}"),
                "published_at": published,
            })

        return posts

    async def ingest_supertopic(self, db: Database, topic_id: str, topic_name: str) -> int:
        """Ingest posts from a super topic. Returns count of new posts."""
        posts = await self._fetch_supertopic(topic_id)
        new_count = 0

        for raw in posts:
            import json as _json
            image_urls_json = _json.dumps(raw.get("image_urls", [])) if raw.get("image_urls") else None
            video_page_urls_json = _json.dumps(raw.get("video_page_urls", [])) if raw.get("video_page_urls") else None
            video_posters_json = _json.dumps(raw.get("video_posters", [])) if raw.get("video_posters") else None
            post = Post(
                weibo_id=raw["weibo_id"],
                source="supertopic",
                author_name=raw["author_name"],
                author_uid=raw.get("author_uid", ""),
                content=raw["content"],
                content_hash=Post.compute_hash(raw["content"]),
                image_urls=image_urls_json,
                video_urls=None,
                video_page_urls=video_page_urls_json,
                video_posters=video_posters_json,
                url=raw["url"],
                published_at=raw["published_at"],
            )
            inserted = await db.insert_post(post)
            if inserted:
                new_count += 1

        logger.info("rsshub.supertopic_ingested", topic=topic_name, new_posts=new_count)
        return new_count

    async def close(self) -> None:
        await self.client.aclose()
