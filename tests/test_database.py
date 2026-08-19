import pytest
from datetime import UTC, datetime
from decimal import Decimal
from pydantic import HttpUrl

from app.database import get_engine, get_sessionmaker, init_db, upsert_tender, get_metrics
from app.models import Tender, TenderClassification


@pytest.mark.anyio
async def test_db_upsert_and_dedupe():
    engine = get_engine("sqlite+aiosqlite:///:memory:")
    await init_db(engine)
    sessionmaker = get_sessionmaker(engine)

    tender1 = Tender(
        external_id="1001",
        source="easuz",
        source_url=HttpUrl("https://easuz.mosreg.ru/tenders/1001"),
        title="Снос аварийного дома",
        customer="УКС Мытищи",
        price=Decimal("5000000.00"),
        status="Прием заявок",
    )

    async with sessionmaker() as session:
        record, created = await upsert_tender(session, tender1)
        assert created is True
        assert record.external_id == "1001"
        assert record.price == Decimal("5000000.00")

    # Idempotent second upsert with classification
    classification = TenderClassification(
        is_relevant=True,
        category="demolition",
        work_types=["снос здания"],
        confidence=0.95,
        reason="Прямое указание на снос",
    )

    async with sessionmaker() as session:
        record2, created2 = await upsert_tender(session, tender1, classification)
        assert created2 is False
        assert record2.is_relevant is True
        assert record2.category_ai == "demolition"
        assert record2.match_score == 0.95

    # Check metrics
    async with sessionmaker() as session:
        metrics = await get_metrics(session)
        assert metrics["total_scanned"] == 1
        assert metrics["unique"] == 1
        assert metrics["relevant"] == 1

    await engine.dispose()
