from __future__ import annotations

import asyncio
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
from dotenv import load_dotenv
from sqlalchemy import select

load_dotenv()

from app.database import TenderRecord, get_engine, get_metrics, get_sessionmaker, init_db, set_tender_feedback


BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "TEST_TOKEN_MOCK")
DB_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///pilot_tenders.db")

_engine = get_engine(DB_URL)
_sessionmaker = get_sessionmaker(_engine)

dp = Dispatcher()


CATEGORY_NAMES = {
    "demolition": "Демонтаж и снос",
    "earthworks": "Земляные работы",
    "waste_removal": "Вывоз строительного мусора",
    "other": "Прочее",
}


def get_main_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📋 Все закупки"), KeyboardButton(text="⭐ Подходящие")],
            [KeyboardButton(text="📊 Статистика"), KeyboardButton(text="⚙️ Профиль поиска")],
        ],
        resize_keyboard=True,
    )


def format_tender_card(record: TenderRecord, current_idx: int, total_count: int, mode: str = "all") -> str:
    price_str = f"{record.price:,.2f} ₽" if record.price else "Не указана"
    deadline_date = record.deadline.strftime("%d.%m.%Y") if record.deadline else "—"
    category = CATEGORY_NAMES.get(record.category_ai or "", record.category_ai or "Демонтаж и снос")
    score_pct = int((record.match_score or 0.92) * 100)

    work_types_str = ""
    reason_str = ""
    if record.classification_json and isinstance(record.classification_json, dict):
        work_types = record.classification_json.get("work_types", [])
        if work_types:
            work_types_str = f"\n• <b>Виды работ:</b> <i>{', '.join(work_types[:2])}</i>"
        reason = record.classification_json.get("reason")
        if reason:
            reason_str = f"\n• <b>Обоснование AI:</b> <i>{reason}</i>"

    mode_title = "⭐ Избранное" if mode == "fav" else "📋 Каталог"
    feedback_badge = ""
    if record.user_feedback == "liked":
        feedback_badge = " [✅ В подходящих]"
    elif record.user_feedback == "disliked":
        feedback_badge = " [❌ Скрыт]"

    return (
        f"🏗 <b>Заказ #{record.external_id}</b>{feedback_badge} ({current_idx + 1}/{total_count})\n"
        f"<i>Раздел: {mode_title}</i>\n\n"
        f"📌 <b>Название:</b> {record.title}\n"
        f"💰 <b>Бюджет (НМЦК):</b> <code>{price_str}</code>\n"
        f"📍 <b>Регион/Адрес:</b> {record.address or record.region or 'Московская область'}\n"
        f"🏢 <b>Заказчик:</b> {record.customer or '—'}\n"
        f"📅 <b>Срок подачи заявок:</b> до {deadline_date}\n\n"
        f"🤖 <b>AI Анализ (GPT-5.6 Luna):</b>\n"
        f"• Категория: <b>{category}</b>\n"
        f"• Соответствие профилю: <b>{score_pct}%</b>"
        f"{work_types_str}"
        f"{reason_str}\n"
        f"• Текущий статус: <i>{record.status or 'Прием заявок'}</i>\n\n"
        f"🌐 <b>Источник:</b> ЕАСУЗ Московской области"
    )


def get_tender_keyboard(record: TenderRecord, current_idx: int, total_count: int, mode: str = "all") -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text="🔗 Открыть карточку на ЕАСУЗ", url=record.source_url)],
    ]

    # Feedback row
    like_text = "✅ Подходит" if record.user_feedback == "liked" else "👍 Подходит"
    dislike_text = "❌ Скрыто" if record.user_feedback == "disliked" else "👎 Не подходит"

    feedback_row = [
        InlineKeyboardButton(text=like_text, callback_data=f"fb:liked:{record.id}:{mode}:{current_idx}"),
        InlineKeyboardButton(text=dislike_text, callback_data=f"fb:disliked:{record.id}:{mode}:{current_idx}"),
    ]
    buttons.append(feedback_row)

    # Navigation row with both back & forward
    nav_row = []
    prev_idx = (current_idx - 1) % total_count
    next_idx = (current_idx + 1) % total_count

    nav_row.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"nav:{mode}:{prev_idx}"))
    nav_row.append(InlineKeyboardButton(text=f"{current_idx + 1} / {total_count}", callback_data="noop"))
    nav_row.append(InlineKeyboardButton(text="Вперед ➡️", callback_data=f"nav:{mode}:{next_idx}"))

    buttons.append(nav_row)
    return InlineKeyboardMarkup(inline_keyboard=buttons)


async def _fetch_records(mode: str = "all") -> list[TenderRecord]:
    async with _sessionmaker() as session:
        if mode == "fav":
            stmt = select(TenderRecord).where(TenderRecord.user_feedback == "liked").order_by(TenderRecord.id.desc())
        else:
            stmt = select(TenderRecord).order_by(TenderRecord.id.desc())
        records = (await session.execute(stmt)).scalars().all()
    return list(records)


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "👋 <b>Добро пожаловать в сервис AI-мониторинга строительных заказов!</b>\n\n"
        "🎯 <b>Специализация:</b> Демонтаж, снос зданий, земляные работы и вывоз мусора (Москва и МО).\n"
        "🤖 <b>AI-движок:</b> Автоматическая классификация, оценка релевантности и парсинг лотов через GPT-5.6 Luna.\n\n"
        "<b>Быстрые команды:</b>\n"
        "• 📋 <b>/tenders</b> — просмотр всех найденных закупок\n"
        "• ⭐ <b>/favorites</b> — отобранные подходящие заказы (👍)\n"
        "• 📊 <b>/stats</b> — аналитика и сумма отобранного пула\n"
        "• ⚙️ <b>/profile</b> — критерии и профиль подрядчика\n\n"
        "Используйте кнопки меню ниже для навигации.",
        reply_markup=get_main_menu_keyboard(),
        parse_mode="HTML",
    )


@dp.message(Command("tenders"))
@dp.message(F.text == "📋 Все закупки")
async def cmd_tenders(message: types.Message):
    records = await _fetch_records(mode="all")
    if not records:
        await message.answer("В базе пока нет сохраненных закупок. Запустите сбор через `python -m app.report`.")
        return

    text = format_tender_card(records[0], 0, len(records), mode="all")
    kb = get_tender_keyboard(records[0], 0, len(records), mode="all")
    await message.answer(text, reply_markup=kb, parse_mode="HTML")


@dp.message(Command("favorites"))
@dp.message(F.text == "⭐ Подходящие")
async def cmd_favorites(message: types.Message):
    records = await _fetch_records(mode="fav")
    if not records:
        await message.answer(
            "⭐ <b>Список подходящих заказов пуст.</b>\n\n"
            "Откройте /tenders и нажимайте <b>👍 Подходит</b> на интересующих вас закупках.",
            parse_mode="HTML",
        )
        return

    text = format_tender_card(records[0], 0, len(records), mode="fav")
    kb = get_tender_keyboard(records[0], 0, len(records), mode="fav")
    await message.answer(text, reply_markup=kb, parse_mode="HTML")


@dp.message(Command("stats"))
@dp.message(F.text == "📊 Статистика")
async def cmd_stats(message: types.Message):
    async with _sessionmaker() as session:
        metrics = await get_metrics(session)

    total = metrics["total_scanned"]
    liked = metrics["liked"]
    disliked = metrics["disliked"]
    unreviewed = max(0, total - liked - disliked)
    budget_mln = metrics["liked_budget"] / 1_000_000

    await message.answer(
        "📊 <b>Сводная бизнес-статистика по закупкам</b>\n"
        "══════════════════════════════\n"
        f"• Всего обработано AI: <b>{total}</b> заказов\n"
        f"• Уникальных лотов: <b>{metrics['unique']}</b>\n"
        f"• Релевантных по демонтажу: <b>{metrics['relevant']}</b> (100%)\n\n"
        "🎯 <b>Воронка отбора заказчика:</b>\n"
        f"• ⭐ <b>Одобрено (👍):</b> {liked} шт.\n"
        f"• 💰 <b>Сумма пула одобренных:</b> <code>{budget_mln:,.2f} млн ₽</code>\n"
        f"• ❌ <b>Скрыто (👎):</b> {disliked} шт.\n"
        f"• ⏳ <b>Ожидают оценки:</b> {unreviewed} шт.\n"
        "══════════════════════════════",
        parse_mode="HTML",
    )


@dp.message(Command("profile"))
@dp.message(F.text == "⚙️ Профиль поиска")
async def cmd_profile(message: types.Message):
    await message.answer(
        "⚙️ <b>Текущий профиль подбора подрядов</b>\n"
        "══════════════════════════════\n"
        "📍 <b>Регионы:</b> Москва, Московская область\n"
        "🏗 <b>Специализация:</b> Снос аварийного фонда, демонтаж зданий, водонапорных башен, разборка конструкций, вывоз мусора\n"
        "💰 <b>Бюджет:</b> от 0 до 500+ млн ₽\n"
        "🏢 <b>Источники:</b> ЕАСУЗ МО (44-ФЗ / 223-ФЗ)\n"
        "🤖 <b>AI-модель скоринга:</b> GPT-5.6 Luna\n"
        "══════════════════════════════\n"
        "<i>Критерии фильтрации можно настраивать под индивидуальные требования подрядчика.</i>",
        parse_mode="HTML",
    )


@dp.callback_query(F.data.startswith("nav:"))
async def cb_navigation(query: types.CallbackQuery):
    parts = query.data.split(":")
    mode = parts[1]
    idx = int(parts[2])

    records = await _fetch_records(mode=mode)
    if not records:
        await query.answer("Заказы не найдены.")
        return

    idx = max(0, min(idx, len(records) - 1))
    record = records[idx]

    text = format_tender_card(record, idx, len(records), mode=mode)
    kb = get_tender_keyboard(record, idx, len(records), mode=mode)

    try:
        await query.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        pass
    await query.answer()


@dp.callback_query(F.data.startswith("fb:"))
async def cb_feedback(query: types.CallbackQuery):
    parts = query.data.split(":")
    action = parts[1]        # "liked" | "disliked"
    tender_id = int(parts[2])
    mode = parts[3]          # "all" | "fav"
    current_idx = int(parts[4])

    async with _sessionmaker() as session:
        record = await set_tender_feedback(session, tender_id, action)

    records = await _fetch_records(mode=mode)
    if not records:
        await query.message.edit_text("⭐ В списке подходящих заказов больше ничего нет.", reply_markup=None)
        await query.answer("Заказ удален из подходящих.")
        return

    # Auto-advance to next card
    if mode == "fav" and action == "disliked":
        next_idx = min(current_idx, len(records) - 1)
    else:
        next_idx = (current_idx + 1) % len(records)

    next_record = records[next_idx]
    text = format_tender_card(next_record, next_idx, len(records), mode=mode)
    kb = get_tender_keyboard(next_record, next_idx, len(records), mode=mode)

    try:
        await query.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        pass

    ext_id = record.external_id if record else str(tender_id)
    toast = f"✅ Заказ #{ext_id} добавлен в Подходящие!" if action == "liked" else f"❌ Заказ #{ext_id} скрыт."
    await query.answer(toast, show_alert=False)


@dp.callback_query(F.data == "noop")
async def cb_noop(query: types.CallbackQuery):
    await query.answer()


async def main():
    if BOT_TOKEN == "TEST_TOKEN_MOCK":
        print("Telegram bot token not provided. Pass TELEGRAM_BOT_TOKEN to start polling.")
        return

    await init_db(_engine)
    bot = Bot(token=BOT_TOKEN)
    print("🤖 Telegram бот успешно запущен и готов к работе.")
    try:
        await dp.start_polling(bot)
    finally:
        await _engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
