from __future__ import annotations

import json
import os
import re
from decimal import Decimal

import httpx
from dotenv import load_dotenv

from app.models import Tender, TenderClassification


DEMOLITION_KEYWORDS = (
    "снос",
    "демонтаж",
    "разборк",
    "ликвидаци",
    "аварийного фонда",
    "аварийного жил",
    "аварийных зданий",
    "аварийного дома",
    "сносу",
    "демонтажу",
)

EXCLUSION_KEYWORDS = (
    "поставка",
    "программного обеспечения",
    "медицинск",
    "питани",
    "канцтовар",
    "бумаг",
    "охрана",
)


def rule_based_classify(tender: Tender) -> TenderClassification:
    """Fast, deterministic, token-free classifier for baseline accuracy and offline testing."""
    text = f"{tender.title} {tender.description or ''} {tender.raw_text or ''}".lower()

    if any(ex in text for ex in EXCLUSION_KEYWORDS) and not any(kw in tender.title.lower() for kw in DEMOLITION_KEYWORDS):
        return TenderClassification(
            is_relevant=False,
            category="other",
            confidence=0.9,
            reason="Закупка не относится к демонтажным или строительным работам.",
        )

    matched_kws = [kw for kw in DEMOLITION_KEYWORDS if kw in text]
    if not matched_kws:
        return TenderClassification(
            is_relevant=False,
            category="other",
            confidence=0.85,
            reason="В тексте и названии отсутствуют ключевые слова демонтажа или сноса.",
        )

    # Determine object type
    object_type = "structure"
    if any(w in text for w in ("жил", "дом", "квартир")):
        object_type = "residential"
    elif any(w in text for w in ("здани", "строени", "корпус", "сооружени")):
        object_type = "commercial"
    elif any(w in text for w in ("труб", "теплотрасс", "коллектор", "дорог", "мост")):
        object_type = "infrastructure"

    work_types = []
    if "снос" in text:
        work_types.append("снос зданий и сооружений")
    if "демонтаж" in text:
        work_types.append("демонтаж конструкций")
    if "мусор" in text or "отход" in text:
        work_types.append("вывоз и утилизация строительного мусора")
    if not work_types:
        work_types.append("демонтажные работы")

    # City / Location heuristics
    city = None
    city_match = re.search(r"г\.\s*([А-Яа-я\-]+)", tender.title + " " + (tender.address or ""))
    if city_match:
        city = city_match.group(1)

    return TenderClassification(
        is_relevant=True,
        category="demolition",
        work_types=work_types,
        region=tender.region or "Московская область",
        city=city,
        address=tender.address,
        object_type=object_type,
        budget_min=tender.price,
        budget_max=tender.price,
        confidence=0.92,
        reason=f"Обнаружены профильные формулировки сноса/демонтажа ({', '.join(matched_kws[:3])}).",
    )


load_dotenv()


class HybridClassifier:
    """Hybrid classifier that uses rule pre-filter and calls LLM when configured/needed."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
    ) -> None:
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        self.base_url = (base_url or os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
        self.model = model or os.getenv("OPENAI_MODEL") or "openai/gpt-5.6-luna"

    async def classify(self, tender: Tender) -> TenderClassification:
        # 1. Rule prefilter
        baseline = rule_based_classify(tender)

        # If no API key configured, return verified baseline
        if not self.api_key:
            return baseline

        # 2. LLM classification if API key is present
        try:
            prompt = f"""Ты — эксперт по анализу строительных закупок. Проанализируй закупку и верни строгий JSON по схеме.
Название: {tender.title}
Заказчик: {tender.customer or 'Не указан'}
Цена: {tender.price or 'Не указана'}
Адрес/Регион: {tender.address or tender.region or 'Не указан'}
Текст: {(tender.raw_text or '')[:1500]}

Верни JSON с полями:
- is_relevant (boolean): true если это демонтаж, снос, разборка, вывоз мусора после сноса
- category (string): demolition / earthworks / waste_removal / other
- work_types (array of string): список работ
- region (string or null)
- city (string or null)
- address (string or null)
- object_type (residential / commercial / industrial / structure / infrastructure)
- budget_min (number or null)
- budget_max (number or null)
- area_sqm (number or null)
- volume_cbm (number or null)
- requirements (array of string)
- confidence (float 0..1)
- reason (string): краткое пояснение на русском языке
"""
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json={
                        "model": self.model,
                        "messages": [
                            {"role": "system", "content": "You are a specialized tender parser. Respond with valid JSON only."},
                            {"role": "user", "content": prompt},
                        ],
                        "response_format": {"type": "json_object"},
                    },
                )
                if resp.status_code == 200:
                    data = resp.json()
                    content = data["choices"][0]["message"]["content"]
                    parsed = json.loads(content)
                    return TenderClassification.model_validate(parsed)
        except Exception:
            pass

        return baseline
