"""DeepSeek AI processor — translates, summarizes, and categorizes Weibo posts.

Uses DeepSeek's OpenAI-compatible API. Each post gets:
  1. Category classification (car / robot / flying_car / financial / rumor / general)
  2. Relevance score (0–100)
  3. English title
  4. English summary (2–3 sentences)
  5. Full English translation
"""

from __future__ import annotations

import asyncio
import json
import os

import structlog
from openai import AsyncOpenAI

from src.store.models import Database, Post

logger = structlog.get_logger()

# Tuned for XPeng monitoring — categories and their descriptions
CATEGORIES = [
    "car",
    "robot",
    "flying_car",
    "financial",
    "rumor",
    "general",
]

SYSTEM_PROMPT = """You are an AI editor for EVConduit.com, a news site covering XPeng (小鹏汽车).

Your job: process a Chinese Weibo post about XPeng and output structured JSON.

Rules:
- "category": one of ["car", "robot", "flying_car", "financial", "rumor", "general"]
  * "car" = XPeng vehicles (G6, G9, P7, X9, MONA, etc.), autonomous driving (XNGP), deliveries
  * "robot" = XPeng robotics, robot horses, humanoid robots
  * "flying_car" = XPeng HT Aero flying cars, eVTOL
  * "financial" = earnings, stock, delivery numbers, funding
  * "rumor" = unverified claims, speculation, leaks that need skepticism
  * "general" = brand mentions, community chatter, other
- "relevance_score": integer 0–100. How important is this for an XPeng-focused news site?
  * 90–100: Major announcement, official news, significant event
  * 70–89: Interesting analysis, notable community post, review
  * 50–69: Relevant mention but not primary topic
  * 30–49: Tangential mention
  * 0–29: Barely relevant, skip
- "title_en": concise English headline (max 80 chars), news-style
- "summary_en": 2–3 sentence English summary, captures the key point
- "content_en": full English translation of the Weibo post, preserving meaning and tone

Output ONLY valid JSON. No markdown, no code fences."""


class AIProcessor:
    """Batch-processes Weibo posts through DeepSeek for translation and classification."""

    def __init__(self) -> None:
        api_key = os.environ.get("DEEPSEEK_API_KEY")
        if not api_key:
            raise RuntimeError("DEEPSEEK_API_KEY environment variable is required")

        self.client = AsyncOpenAI(
            api_key=api_key,
            base_url="https://api.deepseek.com",
        )
        self.model = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")

    async def _process_one(self, post: Post) -> dict | None:
        """Process a single post through DeepSeek. Returns parsed JSON or None on failure."""
        user_message = f"""Weibo post by @{post.author_name}:
URL: {post.url}
Content: {post.content}"""

        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_message},
                ],
                temperature=0.3,
                max_tokens=1024,
                response_format={"type": "json_object"},
            )

            raw = response.choices[0].message.content
            if not raw:
                logger.warning("ai.empty_response", post_id=post.weibo_id)
                return None

            result = json.loads(raw)

            # Validate required fields
            required = ["category", "relevance_score", "title_en", "summary_en", "content_en"]
            for field in required:
                if field not in result:
                    logger.warning("ai.missing_field", post_id=post.weibo_id, field=field)
                    return None

            # Clamp relevance score
            result["relevance_score"] = max(0, min(100, int(result["relevance_score"])))

            # Validate category
            if result["category"] not in CATEGORIES:
                result["category"] = "general"

            return result

        except json.JSONDecodeError as e:
            logger.warning("ai.json_parse_error", post_id=post.weibo_id, error=str(e))
            return None
        except Exception as e:
            logger.warning("ai.api_error", post_id=post.weibo_id, error=str(e))
            return None

    async def process_batch(self, db: Database, limit: int = 20) -> int:
        """Process unprocessed posts through DeepSeek. Returns count of posts processed."""
        posts = await db.get_unprocessed(limit=limit)

        if not posts:
            logger.debug("ai.no_posts_to_process")
            return 0

        logger.info("ai.processing_batch", count=len(posts))

        processed_count = 0

        for post in posts:
            result = await self._process_one(post)
            if result is None:
                continue

            # Update the post with AI results
            async with db.session_factory() as session:
                p = await session.get(Post, post.id)
                if p:
                    p.category = result["category"]
                    p.relevance_score = result["relevance_score"]
                    p.title_en = result["title_en"]
                    p.summary_en = result["summary_en"]
                    p.content_en = result["content_en"]
                    p.ai_processed = True
                    await session.commit()
                    processed_count += 1

            # Rate limit: DeepSeek allows ~60 RPM on free tier, be conservative
            await asyncio.sleep(0.5)

        logger.info("ai.batch_complete", processed=processed_count, total=len(posts))
        return processed_count
