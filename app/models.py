from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from decimal import Decimal
from pydantic import BaseModel, Field, HttpUrl, computed_field


def generate_fingerprint(title: str, customer: str | None, budget: Decimal | None) -> str:
    """Deterministic hash for deduplication across sources."""
    norm_title = re.sub(r"[^\w\s]", "", title.lower())
    norm_title = re.sub(r"\s+", " ", norm_title).strip()
    norm_cust = re.sub(r"[^\w\s]", "", (customer or "").lower())
    norm_cust = re.sub(r"\s+", " ", norm_cust).strip()
    norm_budget = str(int(budget)) if budget is not None else ""
    raw = f"{norm_title}|{norm_cust}|{norm_budget}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class Tender(BaseModel):
    external_id: str
    source: str = "easuz"
    source_url: HttpUrl
    title: str
    description: str | None = None
    category: str | None = None
    region: str | None = "Московская область"
    city: str | None = None
    address: str | None = None
    budget_min: Decimal | None = None
    budget_max: Decimal | None = None
    price: Decimal | None = None
    area: float | None = None
    volume: float | None = None
    start_date: datetime | None = None
    deadline: datetime | None = None
    customer: str | None = None
    customer_inn: str | None = None
    published_at: datetime | None = None
    found_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    collected_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    status: str | None = None
    eis_number: str | None = None
    raw_text: str | None = None

    @computed_field
    @property
    def fingerprint(self) -> str:
        return generate_fingerprint(self.title, self.customer, self.price or self.budget_max)


class CategorySnapshot(BaseModel):
    category_id: int
    category_url: HttpUrl
    total_found: int | None = None
    tender_urls: list[HttpUrl]


class TenderClassification(BaseModel):
    is_relevant: bool = Field(description="Подходит ли под демонтаж / снос / разборку")
    category: str = Field(description="demolition | earthworks | waste_removal | other")
    work_types: list[str] = Field(default_factory=list, description="Типы работ (демонтаж фундамента, снос здания и т.д.)")
    region: str | None = Field(default=None, description="Регион проведения работ")
    city: str | None = Field(default=None, description="Город или населенный пункт")
    address: str | None = Field(default=None, description="Точный адрес объекта")
    object_type: str | None = Field(default=None, description="residential | commercial | industrial | structure | infrastructure")
    budget_min: Decimal | None = None
    budget_max: Decimal | None = None
    area_sqm: float | None = Field(default=None, description="Площадь в м² если есть")
    volume_cbm: float | None = Field(default=None, description="Объем в м³ если есть")
    requirements: list[str] = Field(default_factory=list, description="Спец. требования (СРО, спецтехника, лицензии)")
    confidence: float = Field(ge=0.0, le=1.0, description="Уверенность AI от 0 до 1")
    reason: str = Field(description="Краткое обоснование решения")


class MatchProfile(BaseModel):
    regions: list[str] = Field(default_factory=lambda: ["Москва", "Московская область"])
    categories: list[str] = Field(default_factory=lambda: ["demolition"])
    min_budget: Decimal | None = None
    max_budget: Decimal | None = None
    allowed_object_types: list[str] = Field(default_factory=list)


class MatchResult(BaseModel):
    is_matched: bool
    score: float = Field(ge=0.0, le=1.0)
    reason: str
