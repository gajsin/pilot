from datetime import UTC, datetime
from decimal import Decimal

from app.bot import format_tender_card, get_tender_keyboard
from app.database import TenderRecord


def test_bot_formatting_and_keyboard():
    rec = TenderRecord(
        id=1,
        source="easuz",
        external_id="529951",
        fingerprint="abc",
        source_url="https://easuz.mosreg.ru/tenders/529951",
        title="Снос аварийного дома",
        customer="УКС Шатура",
        price=Decimal("1732083.74"),
        status="Прием заявок",
        region="Московская область",
        category_ai="demolition",
        match_score=0.95,
        published_at=datetime(2026, 4, 30, 7, 19, tzinfo=UTC),
        deadline=datetime(2026, 5, 8, 7, 0, tzinfo=UTC),
    )

    card = format_tender_card(rec, 0, 5)
    assert "Заказ #529951" in card
    assert "1,732,083.74 ₽" in card
    assert "УКС Шатура" in card
    assert "95%" in card

    kb = get_tender_keyboard(rec, 0, 5, mode="all")
    assert len(kb.inline_keyboard) == 3
    assert kb.inline_keyboard[0][0].url == "https://easuz.mosreg.ru/tenders/529951"
    # Row 1 is feedback (like / dislike)
    assert "fb:liked:1:all:0" in kb.inline_keyboard[1][0].callback_data
    # Row 2 is navigation (prev, counter, next)
    assert kb.inline_keyboard[2][0].text == "⬅️ Назад"
    assert kb.inline_keyboard[2][2].text == "Вперед ➡️"
