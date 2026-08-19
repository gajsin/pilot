from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Sequence

from app.adapters.easuz import EasuzAdapter, EasuzConfig
from app.ai.classifier import HybridClassifier
from app.database import get_engine, get_sessionmaker, init_db, upsert_tender
from app.matching import match_tender
from app.models import MatchProfile, MatchResult, Tender, TenderClassification


@dataclass(slots=True)
class PipelineResult:
    scanned_total: int
    unique_count: int
    relevant_count: int
    matched_count: int
    items: list[dict]


async def run_pipeline(
    category_id: int = 13987,
    limit: int | None = 20,
    max_pages: int = 2,
    db_url: str | None = None,
    profile: MatchProfile | None = None,
    classifier: HybridClassifier | None = None,
) -> PipelineResult:
    """Runs end-to-end pipeline: fetch -> dedupe -> AI classify -> match -> save to DB."""
    engine = get_engine(db_url)
    await init_db(engine)
    sessionmaker = get_sessionmaker(engine)
    classifier = classifier or HybridClassifier()
    user_profile = profile or MatchProfile()

    # 1. Fetch from source
    async with EasuzAdapter() as adapter:
        snapshot, tenders = await adapter.fetch_category_tenders(
            category_id,
            limit=limit,
            max_pages=max_pages,
        )

    seen_fingerprints: set[str] = set()
    processed_items: list[dict] = []
    unique_count = 0
    relevant_count = 0
    matched_count = 0

    async with sessionmaker() as session:
        for tender in tenders:
            is_unique = tender.fingerprint not in seen_fingerprints
            if is_unique:
                seen_fingerprints.add(tender.fingerprint)
                unique_count += 1

            # 2. AI Classification
            classification = await classifier.classify(tender)
            if classification.is_relevant:
                relevant_count += 1

            # 3. Matching
            match_res = match_tender(tender, classification, user_profile)
            if match_res.is_matched:
                matched_count += 1

            # 4. Save to Database
            record, _ = await upsert_tender(session, tender, classification)

            processed_items.append({
                "external_id": tender.external_id,
                "title": tender.title,
                "customer": tender.customer,
                "price": float(tender.price) if tender.price else None,
                "url": str(tender.source_url),
                "is_relevant": classification.is_relevant,
                "is_matched": match_res.is_matched,
                "match_score": match_res.score,
                "match_reason": match_res.reason,
                "category_ai": classification.category,
                "work_types": classification.work_types,
                "published_at": tender.published_at.isoformat() if tender.published_at else None,
                "deadline": tender.deadline.isoformat() if tender.deadline else None,
            })

    await engine.dispose()

    return PipelineResult(
        scanned_total=len(tenders),
        unique_count=unique_count,
        relevant_count=relevant_count,
        matched_count=matched_count,
        items=processed_items,
    )
