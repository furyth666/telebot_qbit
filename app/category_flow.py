from __future__ import annotations

import asyncio
import logging
from html import escape

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import Application

from app.config import Settings
from app.formatters import _short_hash
from app.llm_classifier import classify_torrent
from app.qbit_client import QbitClient, TorrentCategory, TorrentSummary


_CATEGORY_BUTTONS_PER_ROW = 2


def _category_choice_keyboard(
    torrent_hash: str,
    choices: list[str],
) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(
            category or "保持未分类",
            callback_data=f"tor:cat:all:{torrent_hash}:{index}",
        )
        for index, category in enumerate(choices)
    ]
    rows = [
        buttons[index : index + _CATEGORY_BUTTONS_PER_ROW]
        for index in range(0, len(buttons), _CATEGORY_BUTTONS_PER_ROW)
    ]
    return InlineKeyboardMarkup(rows)


def _category_choices(categories: list[TorrentCategory]) -> list[str]:
    choices = [item.name for item in categories if item.name]
    return ["", *choices]


def _canonical_llm_category(
    category: str,
    allowed_categories: set[str],
) -> str:
    stripped = category.strip()
    by_casefold = {item.casefold(): item for item in allowed_categories}
    return by_casefold.get(stripped.casefold(), stripped)


async def _register_category_choices(
    application: Application,
    torrent_hash: str,
    choices: list[str],
) -> bool:
    lock = application.bot_data.get("category_prompt_lock")
    if lock is None:
        lock = asyncio.Lock()
        application.bot_data["category_prompt_lock"] = lock

    async with lock:
        prompted: set[str] = application.bot_data.setdefault(
            "prompted_category_hashes",
            set(),
        )
        if torrent_hash in prompted:
            return False
        pending: dict[str, list[str]] = application.bot_data.setdefault(
            "pending_category_choices",
            {},
        )
        pending[torrent_hash] = choices
        prompted.add(torrent_hash)
        return True


async def _send_category_prompt(
    application: Application,
    qbit: QbitClient,
    item: TorrentSummary,
    *,
    chat_id: int,
) -> None:
    categories = await qbit.list_categories()
    choices = _category_choices(categories)
    if not await _register_category_choices(application, item.hash, choices):
        return

    await application.bot.send_message(
        chat_id=chat_id,
        text=(
            "<b>请选择移动到哪个分类</b>\n"
            f"📦 <b>{escape(item.name)}</b>\n"
            f"🔑 <code>{escape(_short_hash(item.hash))}</code>"
        ),
        parse_mode=ParseMode.HTML,
        reply_markup=_category_choice_keyboard(item.hash, choices),
    )


async def _auto_apply_llm_category_after_delay(
    application: Application,
    qbit: QbitClient,
    *,
    torrent_hash: str,
    torrent_name: str,
    category: str,
    confidence: float,
    delay_seconds: float,
    chat_id: int,
) -> None:
    try:
        if delay_seconds > 0:
            await asyncio.sleep(delay_seconds)

        lock = application.bot_data.get("category_prompt_lock")
        if lock is None:
            lock = asyncio.Lock()
            application.bot_data["category_prompt_lock"] = lock

        async with lock:
            pending: dict[str, list[str]] = application.bot_data.get(
                "pending_category_choices",
                {},
            )
            if torrent_hash not in pending:
                return
            pending.pop(torrent_hash, None)

        await qbit.set_category(torrent_hash, category)
        label = category or "未分类"
        await application.bot.send_message(
            chat_id=chat_id,
            text=(
                "<b>已按大模型推荐自动分类</b>\n"
                f"📦 <b>{escape(torrent_name)}</b>\n"
                f"🗂️ 分类: <code>{escape(label)}</code>\n"
                f"置信度: <code>{confidence:.2f}</code>\n"
                f"🔑 <code>{escape(_short_hash(torrent_hash))}</code>"
            ),
            parse_mode=ParseMode.HTML,
        )
    except asyncio.CancelledError:
        raise
    except Exception:
        logging.exception("Failed to auto-apply LLM category")


async def _handle_llm_category_torrent(
    application: Application,
    qbit: QbitClient,
    item: TorrentSummary,
    *,
    chat_id: int,
) -> bool:
    settings: Settings = application.bot_data["settings"]
    if not settings.llm_classify_enabled:
        return False

    categories = await qbit.list_categories()
    choices = _category_choices(categories)
    allowed_categories = set(choices)
    if not await _register_category_choices(application, item.hash, choices):
        return True

    try:
        files = await qbit.get_torrent_files(item.hash)
    except Exception:
        logging.exception("Failed to read torrent files before LLM classification")
        files = []

    try:
        decision = await classify_torrent(settings, item, files, categories)
    except Exception:
        logging.exception("LLM category classification failed")
        await application.bot.send_message(
            chat_id=chat_id,
            text=(
                "<b>⚠️ 大模型分类失败</b>\n"
                f"📦 <b>{escape(item.name)}</b>\n"
                "请手动选择分类。"
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=_category_choice_keyboard(item.hash, choices),
        )
        return True

    category = _canonical_llm_category(decision.category, allowed_categories)
    if (
        category not in allowed_categories
        or decision.confidence < settings.llm_min_confidence
    ):
        await application.bot.send_message(
            chat_id=chat_id,
            text=(
                "<b>⚠️ 大模型没有给出可靠分类</b>\n"
                f"📦 <b>{escape(item.name)}</b>\n"
                f"建议: <code>{escape(category or decision.category or '未分类')}</code>\n"
                f"置信度: <code>{decision.confidence:.2f}</code>\n"
                "请手动选择分类。"
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=_category_choice_keyboard(item.hash, choices),
        )
        return True

    label = category or "未分类"
    reason = decision.reason or "未提供理由"
    await application.bot.send_message(
        chat_id=chat_id,
        text=(
            "<b>大模型推荐分类</b>\n"
            f"📦 <b>{escape(item.name)}</b>\n"
            f"🗂️ 推荐: <code>{escape(label)}</code>\n"
            f"置信度: <code>{decision.confidence:.2f}</code>\n"
            f"理由: {escape(reason)}\n"
            f"🔑 <code>{escape(_short_hash(item.hash))}</code>\n"
            f"请在 <code>{settings.llm_auto_apply_delay_seconds:g}</code> 秒内手动选择；"
            "超时将按推荐分类。"
        ),
        parse_mode=ParseMode.HTML,
        reply_markup=_category_choice_keyboard(item.hash, choices),
    )

    if settings.llm_auto_apply_delay_seconds <= 0:
        await _auto_apply_llm_category_after_delay(
            application,
            qbit,
            torrent_hash=item.hash,
            torrent_name=item.name,
            category=category,
            confidence=decision.confidence,
            delay_seconds=settings.llm_auto_apply_delay_seconds,
            chat_id=chat_id,
        )
    else:
        task = asyncio.create_task(
            _auto_apply_llm_category_after_delay(
                application,
                qbit,
                torrent_hash=item.hash,
                torrent_name=item.name,
                category=category,
                confidence=decision.confidence,
                delay_seconds=settings.llm_auto_apply_delay_seconds,
                chat_id=chat_id,
            )
        )
        tasks: set[asyncio.Task] = application.bot_data.setdefault(
            "llm_auto_apply_tasks",
            set(),
        )
        tasks.add(task)
        task.add_done_callback(tasks.discard)
    return True
