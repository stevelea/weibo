"""OCR + translation module — extracts Chinese text from Weibo images and translates to English.

Uses Tesseract OCR for text extraction and DeepSeek for translation.
Images are downloaded from Weibo CDN, OCR'd, then the extracted Chinese text
is translated to English for use as image captions in the Hugo output.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from io import BytesIO

import httpx
import pytesseract
import structlog
from openai import AsyncOpenAI
from PIL import Image

from src.store.models import Database, Post

logger = structlog.get_logger()

TRANSLATE_SYSTEM = """You are a translator for EVConduit.com. Translate Chinese image text to concise English captions.
Rules:
- Output ONLY the English translation — no explanations, no markdown
- If the text is a heading/title, make it a short caption (max 10 words)
- If it's longer descriptive text, summarize into 1-2 sentences
- If the image has no meaningful text, respond with an empty string
- Preserve numbers, product names (G6, P7, MONA, etc.), and percentages"""


class ImageCaptioner:
    """Downloads Weibo images, OCRs Chinese text, and translates to English captions."""

    def __init__(self) -> None:
        self.client = AsyncOpenAI(
            api_key=os.environ["DEEPSEEK_API_KEY"],
            base_url="https://api.deepseek.com",
        )
        self.model = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
        self.http = httpx.AsyncClient(
            timeout=httpx.Timeout(15.0),
            headers={"User-Agent": "Mozilla/5.0 (compatible; evconduit/1.0)"},
        )

    def _local_image_path(self, post_weibo_id: str, index: int, content_type: str) -> str:
        """Generate a local path for a downloaded image."""
        import hashlib
        dir_hash = hashlib.md5(post_weibo_id.encode()).hexdigest()[:8]
        ext = "jpg" if "jpeg" in content_type or "jpg" in content_type else "png"
        return f"/images/{dir_hash}/{index}.{ext}"

    async def _download_image(self, url: str) -> tuple[Image.Image | None, bytes | None, str]:
        """Download an image, return (PIL Image, raw bytes, content_type)."""
        try:
            resp = await self.http.get(
                url,
                headers={"Referer": "https://weibo.com/"},
            )
            resp.raise_for_status()
            content_type = resp.headers.get("content-type", "image/jpeg")
            raw = resp.content
            return Image.open(BytesIO(raw)), raw, content_type
        except Exception as e:
            logger.debug("ocr.download_failed", url=url[:80], error=str(e)[:60])
            return None, None, ""

    async def _ocr_chinese(self, image: Image.Image) -> str:
        """Extract Chinese text from an image using Tesseract OCR."""
        try:
            loop = asyncio.get_running_loop()

            def _ocr():
                return pytesseract.image_to_string(
                    image, lang="chi_sim", config="--psm 6"
                )

            text = await loop.run_in_executor(None, _ocr)
            return text.strip()
        except Exception as e:
            logger.debug("ocr.extraction_failed", error=str(e)[:60])
            return ""

    async def _translate(self, chinese_text: str) -> str:
        """Translate extracted Chinese text to an English caption."""
        if not chinese_text or len(chinese_text) < 3:
            return ""

        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": TRANSLATE_SYSTEM},
                    {"role": "user", "content": chinese_text[:500]},
                ],
                temperature=0.2,
                max_tokens=120,
            )
            caption = response.choices[0].message.content
            return caption.strip() if caption else ""
        except Exception as e:
            logger.debug("ocr.translate_failed", error=str(e)[:60])
            return ""

    async def caption_images(
        self, image_urls: list[str], post_weibo_id: str, static_dir: str
    ) -> tuple[list[str], list[str]]:
        """Process images: download, OCR, translate, save locally.
        Returns (captions, local_paths) parallel to image_urls.
        """
        import os as _os
        captions = []
        local_paths = []

        for i, url in enumerate(image_urls):
            image, raw, content_type = await self._download_image(url)
            if image is None or raw is None:
                captions.append("")
                local_paths.append("")
                continue

            # Resize and save image locally to Hugo static directory
            local_path = self._local_image_path(post_weibo_id, i, content_type)
            full_path = _os.path.join(static_dir, local_path.lstrip("/"))
            _os.makedirs(_os.path.dirname(full_path), exist_ok=True)
            try:
                # Resize to max 1200px wide to save storage
                w, h = image.size
                if w > 1200:
                    ratio = 1200 / w
                    image = image.resize((1200, int(h * ratio)), Image.LANCZOS)
                image.save(full_path, quality=85, optimize=True)
                local_paths.append(local_path)
            except OSError as e:
                logger.debug("ocr.save_failed", path=full_path, error=str(e)[:60])
                local_paths.append("")

            chinese_text = await self._ocr_chinese(image)
            if not chinese_text:
                captions.append("")
                continue

            caption = await self._translate(chinese_text)
            captions.append(caption)
            logger.debug("ocr.captioned", caption=caption[:60])

        return captions, local_paths

    async def _download_and_save(
        self, url: str, post_weibo_id: str, index: int, static_dir: str
    ) -> str:
        """Download a single image/poster and save locally. Returns local path or empty string."""
        _, raw, content_type = await self._download_image(url)
        if raw is None:
            return ""
        local_path = self._local_image_path(post_weibo_id, index, content_type)
        import os as _os
        full_path = _os.path.join(static_dir, local_path.lstrip("/"))
        _os.makedirs(_os.path.dirname(full_path), exist_ok=True)
        try:
            with open(full_path, "wb") as f:
                f.write(raw)
            return local_path
        except OSError as e:
            logger.debug("ocr.save_failed", path=full_path, error=str(e)[:60])
            return ""

    async def process_post(self, post: Post, db: Database, static_dir: str = "/output/static") -> bool:
        """Process a single post's images for captions. Returns True if captions were added."""
        changed = False

        # Process images for OCR captions
        if post.image_urls and not post.image_captions:
            try:
                urls = json.loads(post.image_urls)
            except (json.JSONDecodeError, TypeError):
                urls = []

            if urls:
                logger.debug("ocr.processing_post", post_id=post.weibo_id, image_count=len(urls))
                captions, local_paths = await self.caption_images(urls, post.weibo_id, static_dir)

                async with db.session_factory() as session:
                    p = await session.get(Post, post.id)
                    if p:
                        p.image_captions = json.dumps(captions)
                        p.image_local_paths = json.dumps(local_paths)
                        if p.published_to_site:
                            p.published_to_site = False
                        await session.commit()
                        changed = True

        # Download video posters locally (fixes hotlinking for remote hosts)
        if post.video_posters and not post.video_poster_local:
            try:
                posters = json.loads(post.video_posters)
            except (json.JSONDecodeError, TypeError):
                posters = []

            if posters:
                local_posters = []
                for i, poster_url in enumerate(posters):
                    local_path = await self._download_and_save(
                        poster_url, f"vp_{post.weibo_id}", i, static_dir
                    )
                    local_posters.append(local_path)

                async with db.session_factory() as session:
                    p = await session.get(Post, post.id)
                    if p:
                        p.video_poster_local = json.dumps(local_posters)
                        if p.published_to_site:
                            p.published_to_site = False
                        await session.commit()
                        changed = True

        return changed

    async def process_batch(self, db: Database, limit: int = 5, static_dir: str = "/output/static") -> int:
        """Process posts needing image captions or video poster downloads. Returns count processed."""
        async with db.session_factory() as session:
            from sqlalchemy import select, or_

            result = await session.execute(
                select(Post)
                .where(
                    or_(
                        # Posts needing image OCR captions
                        Post.image_urls.isnot(None) & Post.image_captions.is_(None),
                        # Posts needing video poster download
                        Post.video_posters.isnot(None) & Post.video_poster_local.is_(None),
                    ),
                    Post.ai_processed == True,  # noqa: E712
                )
                .order_by(Post.published_at.desc())
                .limit(limit)
            )
            posts = list(result.scalars().all())

        if not posts:
            return 0

        logger.info("ocr.processing_batch", count=len(posts))
        count = 0
        for post in posts:
            if await self.process_post(post, db, static_dir):
                count += 1
            await asyncio.sleep(0.5)

        logger.info("ocr.batch_complete", processed=count)
        return count

    async def close(self) -> None:
        await self.http.aclose()
