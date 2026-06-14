"""SQLAlchemy async models for the Weibo pipeline."""

from __future__ import annotations

import datetime
import hashlib

from sqlalchemy import DateTime, Float, Integer, String, Text, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Post(Base):
    __tablename__ = "posts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    weibo_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    source: Mapped[str] = mapped_column(String(32), default="rsshub")  # rsshub, crawl
    author_name: Mapped[str] = mapped_column(String(128))
    author_uid: Mapped[str | None] = mapped_column(String(32), nullable=True)
    content: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    image_urls: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON array of image URLs
    image_captions: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON array of translated captions
    image_local_paths: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON array of local image paths
    video_urls: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON array of video source URLs
    video_page_urls: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON array of video.weibo.com/show URLs
    video_posters: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON array of video poster thumbnail URLs
    video_poster_local: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON array of local poster paths
    url: Mapped[str] = mapped_column(String(512))
    published_at: Mapped[datetime.datetime] = mapped_column(DateTime, index=True)
    fetched_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=datetime.datetime.utcnow
    )

    # AI-processed fields (populated by AIProcessor)
    category: Mapped[str | None] = mapped_column(String(32), nullable=True)
    relevance_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    title_en: Mapped[str | None] = mapped_column(String(256), nullable=True)
    summary_en: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_en: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_processed: Mapped[bool] = mapped_column(default=False)

    # Publishing state
    published_to_site: Mapped[bool] = mapped_column(default=False)
    published_at_site: Mapped[datetime.datetime | None] = mapped_column(
        DateTime, nullable=True
    )

    @staticmethod
    def compute_hash(content: str) -> str:
        return hashlib.sha256(content.encode()).hexdigest()

    def __repr__(self) -> str:
        return f"<Post {self.weibo_id} by @{self.author_name}>"


class Database:
    """Async database wrapper."""

    def __init__(self, url: str) -> None:
        self.url = url
        self.engine = create_async_engine(url, echo=False)
        self.session_factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )

    async def init(self) -> None:
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def get_session(self) -> AsyncSession:
        return self.session_factory()

    async def close(self) -> None:
        await self.engine.dispose()

    async def exists_by_hash(self, content_hash: str) -> bool:
        async with self.session_factory() as session:
            result = await session.execute(
                select(Post.id).where(Post.content_hash == content_hash).limit(1)
            )
            return result.scalar() is not None

    async def exists_by_weibo_id(self, weibo_id: str) -> bool:
        async with self.session_factory() as session:
            result = await session.execute(
                select(Post.id).where(Post.weibo_id == weibo_id).limit(1)
            )
            return result.scalar() is not None

    async def insert_post(self, post: Post) -> bool:
        """Insert a post if it doesn't already exist. Returns True if inserted."""
        async with self.session_factory() as session:
            exists = await session.execute(
                select(Post.id).where(Post.weibo_id == post.weibo_id).limit(1)
            )
            if exists.scalar() is not None:
                return False
            session.add(post)
            await session.commit()
            return True

    async def get_unprocessed(self, limit: int = 20) -> list[Post]:
        """Get posts that haven't been AI-processed yet."""
        async with self.session_factory() as session:
            result = await session.execute(
                select(Post)
                .where(Post.ai_processed == False)  # noqa: E712
                .order_by(Post.published_at.desc())
                .limit(limit)
            )
            return list(result.scalars().all())

    async def get_ready_to_publish(self, min_score: float = 50.0) -> list[Post]:
        """Get AI-processed posts ready for publishing."""
        async with self.session_factory() as session:
            result = await session.execute(
                select(Post)
                .where(
                    Post.ai_processed == True,  # noqa: E712
                    Post.published_to_site == False,  # noqa: E712
                    Post.relevance_score >= min_score,
                )
                .order_by(Post.relevance_score.desc())
                .limit(10)
            )
            return list(result.scalars().all())

    async def mark_published(self, post_id: int) -> None:
        async with self.session_factory() as session:
            post = await session.get(Post, post_id)
            if post:
                post.published_to_site = True
                post.published_at_site = datetime.datetime.utcnow()
                await session.commit()
