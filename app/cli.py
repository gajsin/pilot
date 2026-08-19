from __future__ import annotations

import argparse
import asyncio
import json
import sys

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from sqlalchemy import select

from app.adapters.easuz import EasuzAdapter
from app.database import DEFAULT_DB_URL, TenderRecord, get_engine, get_metrics, get_sessionmaker
from app.pipeline import run_pipeline


async def cmd_scan(category_id: int, limit: int) -> None:
    """Smoke scan category from source and print JSON."""
    async with EasuzAdapter() as adapter:
        snapshot, tenders = await adapter.fetch_category_tenders(category_id, limit=limit)

    print(json.dumps({
        "category": snapshot.model_dump(mode="json"),
        "tenders": [t.model_dump(mode="json") for t in tenders],
    }, ensure_ascii=False, indent=2))


async def cmd_report(category_id: int, limit: int, max_pages: int, db_url: str | None = None) -> None:
    """Run full pipeline and dump metrics to report_metrics.json."""
    print(f"=== Запуск конвейера обработки для категории {category_id} (лимит {limit}) ===")
    result = await run_pipeline(
        category_id=category_id,
        limit=limit,
        max_pages=max_pages,
        db_url=db_url or "sqlite+aiosqlite:///pilot_tenders.db",
    )

    print("\n" + "=" * 60)
    print("🎯 ИТОГОВЫЕ МЕТРИКИ ПИЛОТА (ЕАСУЗ МО — Демонтаж/Снос)")
    print("=" * 60)
    print(f"• Всего обработано (found/scanned):  {result.scanned_total}")
    print(f"• Уникальных (unique):                {result.unique_count}")
    print(f"• Релевантных AI (relevant):          {result.relevant_count}")
    print(f"• Подходящих под профиль (matched):   {result.matched_count}")
    print("=" * 60)

    print("\nПримеры отобранных и сохраненных заказов:")
    for idx, item in enumerate(result.items[:5], 1):
        rel_icon = "✅" if item["is_matched"] else "⚠️"
        print(f"\n{idx}. {rel_icon} [{item['external_id']}] {item['title'][:80]}...")
        print(f"   Заказчик: {item['customer']}")
        print(f"   Цена: {item['price']:,.2f} ₽" if item['price'] else "   Цена: не указана")
        print(f"   AI Категория: {item['category_ai']} | Работы: {', '.join(item['work_types'])}")
        print(f"   AI Match Score: {item['match_score']} ({item['match_reason']})")
        print(f"   URL: {item['url']}")

    with open("report_metrics.json", "w", encoding="utf-8") as f:
        json.dump({
            "metrics": {
                "found_total": result.scanned_total,
                "unique": result.unique_count,
                "relevant": result.relevant_count,
                "matched": result.matched_count,
            },
            "sample_orders": result.items,
        }, f, ensure_ascii=False, indent=2)
    print("\nОтчет сохранен в 'report_metrics.json'")


async def cmd_view(db_url: str | None = None) -> None:
    """Display saved database records and metrics."""
    engine = get_engine(db_url or "sqlite+aiosqlite:///pilot_tenders.db")
    sessionmaker = get_sessionmaker(engine)

    async with sessionmaker() as session:
        stmt = select(TenderRecord).order_by(TenderRecord.id.asc())
        records = (await session.execute(stmt)).scalars().all()
        metrics = await get_metrics(session)

        print("=" * 80)
        print(f"📊 СОДЕРЖИМОЕ ТАБЛИЦЫ TENDERS (Всего записей: {len(records)})")
        print(f"   👍 Подходит: {metrics['liked']} | ❌ Скрыто: {metrics['disliked']} | 💰 Пул одобренных: {metrics['liked_budget']:,.2f} ₽")
        print("=" * 80)
        for r in records:
            price_str = f"{r.price:,.2f} ₽" if r.price else "не указана"
            fb_tag = " [👍 ПОДХОДИТ]" if r.user_feedback == "liked" else (" [❌ СКРЫТ]" if r.user_feedback == "disliked" else "")
            print(f"ID: {r.id:2d} | #{r.external_id}{fb_tag} | {price_str:>16} | AI: {r.category_ai} (score {r.match_score})")
            print(f"     Название: {r.title[:75]}...")
            print(f"     Заказчик: {r.customer}")
            print(f"     URL:      {r.source_url}")
            print("-" * 80)

    await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Opportunity Pilot CLI (Tenders Monitor & Classifier)")
    subparsers = parser.add_subparsers(dest="command", help="Available subcommands")

    # scan
    p_scan = subparsers.add_parser("scan", help="Smoke scan from source adapter")
    p_scan.add_argument("--category", type=int, default=13987)
    p_scan.add_argument("--limit", type=int, default=5)

    # report
    p_report = subparsers.add_parser("report", help="Run full pipeline and generate report")
    p_report.add_argument("--category", type=int, default=13987)
    p_report.add_argument("--limit", type=int, default=100)
    p_report.add_argument("--pages", type=int, default=None)
    p_report.add_argument("--db-url", type=str, default=None)

    # view
    p_view = subparsers.add_parser("view", help="View database records and metrics")
    p_view.add_argument("--db-url", type=str, default=None)

    args = parser.parse_args()

    if args.command == "scan":
        asyncio.run(cmd_scan(args.category, args.limit))
    elif args.command == "report":
        pages = args.pages if args.pages is not None else max(1, (args.limit + 9) // 10)
        asyncio.run(cmd_report(args.category, args.limit, pages, args.db_url))
    elif args.command == "view" or args.command is None:
        db_url = getattr(args, "db_url", None)
        asyncio.run(cmd_view(db_url))


if __name__ == "__main__":
    main()
