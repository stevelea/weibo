"""Hugo static site publisher — writes markdown files for evconduit.com.

Generates Hugo-compatible markdown files with frontmatter from processed Weibo posts.
Hugo watches the content directory and rebuilds automatically.

evconduit.com site structure:
  output/
  ├── config.toml          # Hugo site config
  ├── content/
  │   ├── posts/
  │   │   └── <slug>.md    # Individual news posts
  │   └── _index.md
  ├── themes/
  │   └── evconduit/       # Custom theme
  └── public/              # Built site (served by Hugo/Nginx)
"""

from __future__ import annotations

import datetime
import json
import os
import re
from pathlib import Path

import structlog

from src.store.models import Database, Post

logger = structlog.get_logger()


class HugoPublisher:
    """Publishes processed Weibo posts as Hugo markdown files."""

    def __init__(self) -> None:
        self.content_dir = Path(os.environ.get("HUGO_CONTENT_DIR", "output/content"))
        self.posts_dir = self.content_dir / "posts"
        self.posts_dir.mkdir(parents=True, exist_ok=True)

    def _slugify(self, title: str, weibo_id: str) -> str:
        """Generate a URL-friendly slug from title + weibo_id."""
        # Take first 60 chars of title
        base = title[:60].lower()
        # Replace non-alphanumeric with hyphens
        base = re.sub(r"[^a-z0-9]+", "-", base)
        base = base.strip("-")
        # Append last 8 chars of weibo_id for uniqueness
        short_id = weibo_id[-8:]
        return f"{base}-{short_id}" if base else f"weibo-{short_id}"

    def _build_frontmatter(self, post: Post) -> str:
        """Build YAML frontmatter for a Hugo post."""
        date_str = post.published_at.strftime("%Y-%m-%dT%H:%M:%S+08:00")
        summary = (post.summary_en or "").replace('"', "'")
        fm = f"""---
title: "{post.title_en or 'Untitled'}"
description: "{summary}"
date: {date_str}
draft: false
categories: ["{post.category or 'general'}"]
tags: ["xpeng", "{post.category or 'general'}", "weibo"]
source: "Weibo"
source_author: "{post.author_name}"
source_url: "{post.url}"
relevance: {post.relevance_score or 0}
---"""
        return fm

    def _build_content(self, post: Post) -> str:
        """Build the markdown body for a Hugo post."""
        lines = []

        # Summary (plain paragraph, not markdown blockquote)
        if post.summary_en:
            lines.append(post.summary_en)
            lines.append("")

        # Images from the original Weibo post (use local paths if available)
        if post.image_urls:
            try:
                urls = json.loads(post.image_urls)
                captions: list[str] = []
                local_paths: list[str] = []
                if post.image_captions:
                    try:
                        captions = json.loads(post.image_captions)
                    except (json.JSONDecodeError, TypeError):
                        pass
                if post.image_local_paths:
                    try:
                        local_paths = json.loads(post.image_local_paths)
                    except (json.JSONDecodeError, TypeError):
                        pass

                base_path = os.environ.get("SITE_BASE_PATH", "")
                for i, img_url in enumerate(urls[:4]):
                    raw_src = local_paths[i] if i < len(local_paths) and local_paths[i] else img_url
                    src = f"{base_path}{raw_src}" if raw_src.startswith("/images/") else raw_src
                    caption = captions[i] if i < len(captions) else ""
                    alt = caption if caption else "Weibo image"
                    if caption:
                        lines.append(
                            f'<figure class="weibo-image">'
                            f'<img src="{src}" alt="{alt}" loading="lazy">'
                            f'<figcaption>{caption}</figcaption>'
                            f'</figure>'
                        )
                    else:
                        lines.append(
                            f'<figure class="weibo-image">'
                            f'<img src="{src}" alt="{alt}" loading="lazy">'
                            f'</figure>'
                        )
                if urls:
                    lines.append("")
            except (json.JSONDecodeError, TypeError):
                pass

        # Video cards — clickable poster thumbnail linking to Weibo video page
        if post.video_page_urls:
            try:
                vp_urls = json.loads(post.video_page_urls)
                posters: list[str] = []
                local_posters: list[str] = []
                if post.video_posters:
                    try:
                        posters = json.loads(post.video_posters)
                    except (json.JSONDecodeError, TypeError):
                        pass
                if post.video_poster_local:
                    try:
                        local_posters = json.loads(post.video_poster_local)
                    except (json.JSONDecodeError, TypeError):
                        pass

                for i, vp_url in enumerate(vp_urls):
                    # Prefer local poster (no hotlinking), fall back to CDN URL
                    poster = local_posters[i] if i < len(local_posters) and local_posters[i] else (
                        posters[i] if i < len(posters) else ""
                    )
                    lines.append('<div class="video-card">')
                    lines.append(
                        f'<a href="{vp_url}" target="_blank" rel="noopener" '
                        f'class="video-card-link">'
                    )
                    if poster:
                        poster_src = f"{base_path}{poster}" if poster.startswith("/images/") else poster
                        lines.append(f'<img src="{poster_src}" alt="Video thumbnail" loading="lazy">')
                    lines.append('<span class="video-play">▶</span>')
                    lines.append('</a>')
                    lines.append(
                        '<p><a href="' + vp_url + '" target="_blank" rel="noopener">'
                        '🎬 Watch video on Weibo →</a></p>'
                    )
                    lines.append('</div>')
                if vp_urls:
                    lines.append("")
            except (json.JSONDecodeError, TypeError):
                pass

        # Full translated content — padded to avoid Hugo Goldmark buffer boundary bug
        # Goldmark (Hugo's markdown renderer) has a bug where multibyte characters
        # at certain 256-byte alignment boundaries cause "slice bounds out of range".
        # Adding a trailing HTML comment pushes content past the dangerous boundaries.
        if post.content_en:
            lines.append('<div class="post-body">')
            lines.append(post.content_en)
            lines.append('</div>')
            lines.append('<!-- avoid-goldmark-boundary: padding -->')
            lines.append("")

        # Source attribution
        lines.append("---")
        lines.append("")
        lines.append(f"**Source:** [@{post.author_name} on Weibo]({post.url})")
        lines.append(f"**Published:** {post.published_at.strftime('%Y-%m-%d %H:%M')} CST")
        lines.append(f"**Category:** {post.category or 'general'} | **Relevance:** {post.relevance_score}/100")

        return "\n".join(lines)

    async def publish(self, db: Database) -> int:
        """Publish AI-processed, high-relevance posts to Hugo markdown. Returns count published."""
        posts = await db.get_ready_to_publish(min_score=50.0)

        if not posts:
            logger.debug("hugo.no_posts_ready")
            return 0

        published_count = 0

        for post in posts:
            slug = self._slugify(post.title_en or "untitled", post.weibo_id)
            file_path = self.posts_dir / f"{slug}.md"

            frontmatter = self._build_frontmatter(post)
            body = self._build_content(post)
            full_content = frontmatter + "\n\n" + body

            try:
                file_path.write_text(full_content, encoding="utf-8")
                await db.mark_published(post.id)
                published_count += 1
                logger.info("hugo.published", slug=slug, title=post.title_en, score=post.relevance_score)
            except OSError as e:
                logger.error("hugo.write_failed", slug=slug, error=str(e))

        logger.info("hugo.batch_complete", published=published_count, total=len(posts))
        return published_count

    async def ensure_site_config(self) -> None:
        """Create Hugo site config if it doesn't exist."""
        config_path = self.content_dir.parent / "config.toml"

        if config_path.exists():
            return

        config_content = """baseURL = "https://www.evconduit.com/news/"
languageCode = "en-us"
title = "EV Conduit — XPeng News & Community Intelligence"
theme = "evconduit"

[params]
  description = "Real-time XPeng news, community intelligence, and analysis from Weibo."
  author = "EV Conduit"

[taxonomies]
  category = "categories"
  tag = "tags"

[markup]
  [markup.goldmark]
    [markup.goldmark.renderer]
      unsafe = true

[build]
  writeStats = true
"""

        config_path.write_text(config_content, encoding="utf-8")
        logger.info("hugo.config_created", path=str(config_path))

        # Create _index.md for posts
        posts_index = self.posts_dir / "_index.md"
        posts_index.write_text(
            "---\ntitle: \"News Feed\"\ndescription: \"XPeng news, analysis, and community intelligence from Weibo\"\n---\n",
            encoding="utf-8",
        )

        # Create _index.md for homepage
        home_index = self.content_dir / "_index.md"
        home_index.write_text(
            "---\ntitle: \"EV Conduit\"\ndescription: \"Real-time XPeng news and community intelligence\"\n---\n\n## Latest XPeng News from the Weibo Community\n\nCurated, translated, and verified news about XPeng Motors — cars, robots, flying vehicles, and more.\n",
            encoding="utf-8",
        )
