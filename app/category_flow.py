from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from html import escape

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import Application

from app.callback_data import build_category_callback
from app.config import Settings
from app.formatters import short_hash
from app.jav_rules import extract_jav_prefixes
from app.llm_classifier import classify_torrent
from app.qbit_client import QbitClient, TorrentCategory, TorrentSummary
from app.runtime_state import runtime_context


_CATEGORY_BUTTONS_PER_ROW = 2
_JELLYFIN_PREFIX_CACHE_TTL_SECONDS = 6 * 60 * 60
_JELLYFIN_PREFIX_SCAN_LIMIT = 300


@dataclass(frozen=True)
class ManualCategoryChoice:
    torrent_hash: str
    category: str

    @property
    def label(self) -> str:
        return self.category or "未分类"


def category_choice_keyboard(
    torrent_hash: str,
    choices: list[str],
) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(
            category or "保持未分类",
            callback_data=build_category_callback(torrent_hash, index),
        )
        for index, category in enumerate(choices)
    ]
    rows = [
        buttons[index : index + _CATEGORY_BUTTONS_PER_ROW]
        for index in range(0, len(buttons), _CATEGORY_BUTTONS_PER_ROW)
    ]
    return InlineKeyboardMarkup(rows)


def category_choices(categories: list[TorrentCategory]) -> list[str]:
    choices = [item.name for item in categories if item.name]
    return ["", *choices]


def _canonical_llm_category(
    category: str,
    allowed_categories: set[str],
) -> str:
    stripped = category.strip()
    by_casefold = {item.casefold(): item for item in allowed_categories}
    return by_casefold.get(stripped.casefold(), stripped)


async def _jellyfin_jav_prefixes(application: Application) -> list[str]:
    context = runtime_context(application)
    cached = context.get_jellyfin_jav_prefix_cache(
        ttl_seconds=_JELLYFIN_PREFIX_CACHE_TTL_SECONDS,
    )
    if cached is not None:
        return cached

    jellyfin = context.jellyfin
    if not jellyfin.enabled:
        context.set_jellyfin_jav_prefix_cache([])
        return []

    try:
        texts = await jellyfin.list_media_identity_texts(
            limit=_JELLYFIN_PREFIX_SCAN_LIMIT,
        )
    except Exception:
        logging.exception("Failed to extract JAV prefixes from Jellyfin")
        return []

    prefixes = extract_jav_prefixes(texts, context.jav_pattern)
    context.set_jellyfin_jav_prefix_cache(prefixes)
    return prefixes


async def _register_category_choices(
    application: Application,
    torrent_hash: str,
    choices: list[str],
) -> bool:
    context = runtime_context(application)
    lock = context.category_prompt_lock()

    async with lock:
        prompted = context.prompted_category_hashes
        if torrent_hash in prompted:
            return False
        pending = context.pending_category_choices
        pending[torrent_hash] = choices
        prompted.add(torrent_hash)
        return True


async def send_category_prompt(
    application: Application,
    qbit: QbitClient,
    item: TorrentSummary,
    *,
    chat_id: int,
) -> None:
    categories = await qbit.list_categories()
    choices = category_choices(categories)
    if not await _register_category_choices(application, item.hash, choices):
        return

    await application.bot.send_message(
        chat_id=chat_id,
        text=(
            "<b>请选择移动到哪个分类</b>\n"
            f"📦 <b>{escape(item.name)}</b>\n"
            f"🔑 <code>{escape(short_hash(item.hash))}</code>"
        ),
        parse_mode=ParseMode.HTML,
        reply_markup=category_choice_keyboard(item.hash, choices),
    )


async def auto_apply_llm_category_after_delay(
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

        context = runtime_context(application)
        lock = context.category_prompt_lock()

        async with lock:
            pending = context.pending_category_choices
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
                f"🔑 <code>{escape(short_hash(torrent_hash))}</code>"
            ),
            parse_mode=ParseMode.HTML,
        )
    except asyncio.CancelledError:
        raise
    except Exception:
        logging.exception("Failed to auto-apply LLM category")


async def apply_manual_category_choice(
    application: Application,
    qbit: QbitClient,
    *,
    torrent_hash: str,
    category_index: int,
) -> ManualCategoryChoice | None:
    context = runtime_context(application)
    pending = context.pending_category_choices
    choices = pending.get(torrent_hash)
    if not choices or category_index < 0 or category_index >= len(choices):
        return None

    category = choices[category_index]
    await qbit.set_category(torrent_hash, category)
    pending.pop(torrent_hash, None)
    context.prompted_category_hashes.discard(torrent_hash)
    return ManualCategoryChoice(torrent_hash=torrent_hash, category=category)


async def handle_llm_category_torrent(
    application: Application,
    qbit: QbitClient,
    item: TorrentSummary,
    *,
    chat_id: int,
) -> bool:
    context = runtime_context(application)
    settings: Settings = context.settings
    if not settings.llm_classify_enabled:
        return False

    categories = await qbit.list_categories()
    choices = category_choices(categories)
    allowed_categories = set(choices)
    if not await _register_category_choices(application, item.hash, choices):
        return True

    try:
        files = await qbit.get_torrent_files(item.hash)
    except Exception:
        logging.exception("Failed to read torrent files before LLM classification")
        files = []

    try:
        decision = await classify_torrent(
            settings,
            item,
            files,
            categories,
            jav_prefixes=await _jellyfin_jav_prefixes(application),
        )
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
            reply_markup=category_choice_keyboard(item.hash, choices),
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
            reply_markup=category_choice_keyboard(item.hash, choices),
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
            f"🔑 <code>{escape(short_hash(item.hash))}</code>\n"
            f"请在 <code>{settings.llm_auto_apply_delay_seconds:g}</code> 秒内手动选择；"
            "超时将按推荐分类。"
        ),
        parse_mode=ParseMode.HTML,
        reply_markup=category_choice_keyboard(item.hash, choices),
    )

    if settings.llm_auto_apply_delay_seconds <= 0:
        await auto_apply_llm_category_after_delay(
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
            auto_apply_llm_category_after_delay(
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
        tasks = context.llm_auto_apply_tasks
        tasks.add(task)
        task.add_done_callback(tasks.discard)
    return True
