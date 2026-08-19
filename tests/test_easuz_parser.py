from decimal import Decimal

from app.adapters.easuz import EasuzAdapter


CATEGORY_HTML = """
<html><body>
<h1>Работы по демонтажу и сносу зданий и сооружений</h1>
<div>Найдено 406 закупок</div>
<a href="/tenders/500001">Подробнее</a>
<a href="/tenders/500002?x=1">Подробнее</a>
<a href="/tenders/500001">Дубликат ссылки</a>
</body></html>
"""

DETAIL_HTML = """
<html><body><main>
<div>Объект закупки</div>
<h1>Выполнение работ по сносу аварийного здания</h1>
<section><span>Заказчик</span><strong>ГБУ МО ТЕСТ</strong></section>
<section><span>Реестровый номер ЕИС</span><strong>0848500002826000001</strong></section>
<section><span>Статус</span><strong>Прием заявок</strong></section>
<section><span>Начальная цена</span><strong>8 042 000 ₽</strong></section>
<section><span>Размещено</span><strong>19.08.2026, 10:43</strong></section>
<section><span>Подать заявку до</span><strong>26.08.2026, 10:00</strong></section>
</main></body></html>
"""


def test_parse_category():
    snapshot = EasuzAdapter.parse_category_html(13987, CATEGORY_HTML)
    assert snapshot.total_found == 406
    assert len(snapshot.tender_urls) == 2
    assert str(snapshot.tender_urls[0]).endswith("/tenders/500001")


def test_parse_detail():
    tender = EasuzAdapter.parse_tender_html(
        "https://easuz.mosreg.ru/tenders/500001",
        DETAIL_HTML,
    )
    assert tender.external_id == "500001"
    assert tender.title == "Выполнение работ по сносу аварийного здания"
    assert tender.customer == "ГБУ МО ТЕСТ"
    assert tender.eis_number == "0848500002826000001"
    assert tender.status == "Прием заявок"
    assert tender.price == Decimal("8042000")
    assert tender.published_at.year == 2026
    assert tender.deadline.day == 26


def test_real_category_fixture():
    from pathlib import Path
    fixture_path = Path(__file__).parent / "fixtures" / "catalog_13987.html"
    if not fixture_path.exists():
        return
    html = fixture_path.read_text(encoding="utf-8")
    snapshot = EasuzAdapter.parse_category_html(13987, html)
    assert snapshot.category_id == 13987
    assert snapshot.total_found == 406
    assert len(snapshot.tender_urls) == 10
    assert str(snapshot.tender_urls[0]).endswith("/tenders/529951")


def test_real_detail_fixture():
    from pathlib import Path
    fixture_path = Path(__file__).parent / "fixtures" / "tender_529951.html"
    if not fixture_path.exists():
        return
    html = fixture_path.read_text(encoding="utf-8")
    tender = EasuzAdapter.parse_tender_html("https://easuz.mosreg.ru/tenders/529951", html)
    assert tender.external_id == "529951"
    assert "сносу аварийного жилья" in tender.title
    assert "ШАТУРА" in tender.customer
    assert tender.eis_number == "0348600022026000018"
    assert tender.status == "Прием заявок"
    assert tender.price == Decimal("1732083.74")
    assert tender.published_at is not None
    assert tender.deadline is not None

