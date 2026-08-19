from __future__ import annotations

import os
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    select,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import JSON

from app.models import Tender, TenderClassification


DEFAULT_DB_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/opportunity_pilot",
)


class Base(DeclarativeBase):
    pass


class TenderRecord(Base):
    __tablename__ = "tenders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    external_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    customer: Mapped[str | None] = mapped_column(Text, nullable=True)
    customer_inn: Mapped[str | None] = mapped_column(String(32), nullable=True)
    price: Mapped[Decimal | None] = mapped_column(Numeric(15, 2), nullable=True)
    status: Mapped[str | None] = mapped_column(String(128), nullable=True)
    eis_number: Mapped[str | None] = mapped_column(String(128), nullable=True)
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    region: Mapped[str | None] = mapped_column(String(128), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    raw_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    # AI & Classification
    is_relevant: Mapped[bool | None] = mapped_column(Boolean, nullable=True, index=True)
    category_ai: Mapped[str | None] = mapped_column(String(64), nullable=True)
    match_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    classification_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    # User Feedback & Workflow
    user_feedback: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)  # 'liked' | 'disliked' | None

    collected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=func.now(),
    )

    __table_args__ = (
        UniqueConstraint("source", "external_id", name="uq_source_external_id"),
    )


def get_engine(db_url: str | None = None) -> AsyncEngine:
    url = db_url or DEFAULT_DB_URL
    return create_async_engine(url, echo=False)


def get_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def init_db(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def set_tender_feedback(session: AsyncSession, tender_id: int, feedback: str | None) -> TenderRecord | None:
    """Updates user feedback ('liked', 'disliked', None) for a tender."""
    stmt = select(TenderRecord).where(TenderRecord.id == tender_id)
    res = await session.execute(stmt)
    record = res.scalar_one_or_none()
    if record:
        record.user_feedback = feedback
        await session.commit()
        await session.refresh(record)
    return record


async def upsert_tender(session: AsyncSession, tender: Tender, classification: TenderClassification | None = None) -> tuple[TenderRecord, bool]:
    """Upsert tender idempotently. Returns (record, is_created)."""
    stmt = select(TenderRecord).where(
        TenderRecord.source == tender.source,
        TenderRecord.external_id == tender.external_id,
    )
    res = await session.execute(stmt)
    record = res.scalar_one_or_none()

    is_created = False
    if record is None:
        record = TenderRecord(
            source=tender.source,
            external_id=tender.external_id,
            fingerprint=tender.fingerprint,
            source_url=str(tender.source_url),
            title=tender.title,
            customer=tender.customer,
            customer_inn=tender.customer_inn,
            price=tender.price,
            status=tender.status,
            eis_number=tender.eis_number,
            address=tender.address,
            region=tender.region,
            published_at=tender.published_at,
            deadline=tender.deadline,
            raw_text=tender.raw_text,
            collected_at=tender.collected_at,
        )
        session.add(record)
        is_created = True
    else:
        # Update mutable fields
        record.title = tender.title
        record.customer = tender.customer
        record.customer_inn = tender.customer_inn
        record.price = tender.price
        record.status = tender.status
        record.eis_number = tender.eis_number
        record.address = tender.address
        record.deadline = tender.deadline
        record.fingerprint = tender.fingerprint

    if classification:
        record.is_relevant = classification.is_relevant
        record.category_ai = classification.category
        record.match_score = classification.confidence
        record.classification_json = classification.model_dump(mode="json")

    await session.commit()
    await session.refresh(record)
    return record, is_created


async def get_metrics(session: AsyncSession) -> dict[str, Any]:
    total_query = select(func.count(TenderRecord.id))
    unique_fingerprints_query = select(func.count(func.distinct(TenderRecord.fingerprint)))
    relevant_query = select(func.count(TenderRecord.id)).where(TenderRecord.is_relevant.is_(True))
    liked_query = select(func.count(TenderRecord.id)).where(TenderRecord.user_feedback == "liked")
    disliked_query = select(func.count(TenderRecord.id)).where(TenderRecord.user_feedback == "disliked")
    liked_budget_query = select(func.sum(TenderRecord.price)).where(TenderRecord.user_feedback == "liked")

    total = (await session.execute(total_query)).scalar_one() or 0
    unique = (await session.execute(unique_fingerprints_query)).scalar_one() or 0
    relevant = (await session.execute(relevant_query)).scalar_one() or 0
    liked = (await session.execute(liked_query)).scalar_one() or 0
    disliked = (await session.execute(disliked_query)).scalar_one() or 0
    liked_budget = (await session.execute(liked_budget_query)).scalar_one() or Decimal(0)

    return {
        "total_scanned": total,
        "unique": unique,
        "relevant": relevant,
        "liked": liked,
        "disliked": disliked,
        "liked_budget": float(liked_budget) if liked_budget else 0.0,
    }
