from decimal import Decimal
from pydantic import HttpUrl

from app.ai.classifier import rule_based_classify
from app.matching import match_tender
from app.models import MatchProfile, Tender


def test_rule_based_classifier():
    tender_relevant = Tender(
        external_id="1",
        source="easuz",
        source_url=HttpUrl("https://easuz.mosreg.ru/tenders/1"),
        title="Снос и демонтаж аварийного здания школы",
        price=Decimal("12500000.00"),
        customer="Администрация Мытищи",
    )
    res = rule_based_classify(tender_relevant)
    assert res.is_relevant is True
    assert res.category == "demolition"
    assert "снос зданий и сооружений" in res.work_types
    assert res.confidence >= 0.9

    tender_irrelevant = Tender(
        external_id="2",
        source="easuz",
        source_url=HttpUrl("https://easuz.mosreg.ru/tenders/2"),
        title="Поставка офисной бумаги и картриджей",
        price=Decimal("50000.00"),
        customer="Больница №1",
    )
    res_irr = rule_based_classify(tender_irrelevant)
    assert res_irr.is_relevant is False


def test_matching_engine():
    tender = Tender(
        external_id="1",
        source="easuz",
        source_url=HttpUrl("https://easuz.mosreg.ru/tenders/1"),
        title="Снос аварийного дома",
        price=Decimal("2000000.00"),
        region="Московская область",
    )
    classification = rule_based_classify(tender)

    profile = MatchProfile(
        regions=["Московская область"],
        min_budget=Decimal("1000000.00"),
        max_budget=Decimal("5000000.00"),
    )

    match = match_tender(tender, classification, profile)
    assert match.is_matched is True
    assert match.score >= 0.8

    # Outside budget
    strict_profile = MatchProfile(min_budget=Decimal("3000000.00"))
    match_out = match_tender(tender, classification, strict_profile)
    assert match_out.is_matched is False
