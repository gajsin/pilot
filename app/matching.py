from __future__ import annotations

from decimal import Decimal

from app.models import MatchProfile, MatchResult, Tender, TenderClassification


def match_tender(
    tender: Tender,
    classification: TenderClassification | None = None,
    profile: MatchProfile | None = None,
) -> MatchResult:
    """Matches a tender against user profile criteria."""
    user_profile = profile or MatchProfile()

    # 1. Relevance check
    if classification and not classification.is_relevant:
        return MatchResult(
            is_matched=False,
            score=0.1,
            reason="Закупка не относится к профилю демонтажных работ.",
        )

    # 2. Region check
    tender_region = (classification.region if classification else None) or tender.region or ""
    region_match = any(r.lower() in tender_region.lower() for r in user_profile.regions)
    if not region_match and user_profile.regions:
        return MatchResult(
            is_matched=False,
            score=0.3,
            reason=f"Регион ({tender_region}) не входит в фильтр пользователя ({', '.join(user_profile.regions)}).",
        )

    # 3. Budget checks
    price = tender.price or (classification.budget_max if classification else None)
    if price is not None:
        if user_profile.min_budget is not None and price < user_profile.min_budget:
            return MatchResult(
                is_matched=False,
                score=0.4,
                reason=f"Бюджет {price:,.0f} ₽ ниже минимального порога {user_profile.min_budget:,.0f} ₽.",
            )
        if user_profile.max_budget is not None and price > user_profile.max_budget:
            return MatchResult(
                is_matched=False,
                score=0.4,
                reason=f"Бюджет {price:,.0f} ₽ превышает максимальный порог {user_profile.max_budget:,.0f} ₽.",
            )

    # 4. Score calculation
    base_score = 0.7
    if classification:
        base_score = max(base_score, classification.confidence)
        if classification.work_types:
            base_score = min(1.0, base_score + 0.1)

    reasons = []
    if classification and classification.work_types:
        reasons.append(f"Работы: {', '.join(classification.work_types[:2])}")
    if tender.customer:
        reasons.append(f"Заказчик: {tender.customer}")
    if price:
        reasons.append(f"НМЦК: {price:,.2f} ₽")

    reason_str = "Подходит по критериям демонтажа в Мск/МО. " + " | ".join(reasons)
    return MatchResult(is_matched=True, score=round(base_score, 2), reason=reason_str)
