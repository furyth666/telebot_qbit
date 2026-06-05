from __future__ import annotations

import asyncio
import io
import logging
from html import escape

from telegram import InputFile, Update
from telegram.constants import ParseMode
from telegram.ext import Application, ContextTypes

from app.add_links import (
    AddContext,
    _add_torrent_links,
    _extract_torrent_links,
    _format_add_batch_reply,
)
from app.formatters import _format_jellyfin_caption
from app.handler_utils import _require_allowed_user
from app.jav_rules import _extract_jav_lookup_code
from app.jellyfin_client import JellyfinClient, JellyfinItem
from app.jobs import _background_finalize_torrent
from app.qbit_client import QbitClient
from app.runtime_state import _get_jav_pattern


_MAX_BACKGROUND_FINALIZE_CONCURRENCY = 3


def _pick_best_jellyfin_match(code: str, items: list[JellyfinItem]) -> JellyfinItem:
    code_lower = code.lower()
    scored = []
    for item in items:
        name = item.name.lower()
        path = item.path.lower()
        score = 0
        if code_lower == name:
            score += 4
        if code_lower in name:
            score += 3
        if code_lower in path:
            score += 2
        scored.append((score, item))
    scored.sort(key=lambda entry: entry[0], reverse=True)
    return scored[0][1]


def _get_finalize_semaphore(application: Application) -> asyncio.Semaphore:
    semaphore = application.bot_data.get("add_finalize_semaphore")
    if semaphore is None:
        semaphore = asyncio.Semaphore(_MAX_BACKGROUND_FINALIZE_CONCURRENCY)
        application.bot_data["add_finalize_semaphore"] = semaphore
    return semaphore


async def _finalize_added_torrents_batch(
    application: Application,
    qbit: QbitClient,
    contexts: list[AddContext],
    chat_id: int,
) -> None:
    semaphore = _get_finalize_semaphore(application)
    queue: asyncio.Queue[AddContext] = asyncio.Queue()
    for add_context in contexts:
        queue.put_nowait(add_context)

    async def worker() -> None:
        while True:
            try:
                add_context = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            try:
                async with semaphore:
                    await _background_finalize_torrent(
                        application,
                        qbit,
                        add_context,
                        chat_id,
                    )
            except Exception:
                logging.exception("Failed while finalizing added torrent in batch")
            finally:
                queue.task_done()

    worker_count = min(_MAX_BACKGROUND_FINALIZE_CONCURRENCY, len(contexts))
    await asyncio.gather(*(worker() for _ in range(worker_count)))


def _start_add_background_tasks(
    application: Application,
    qbit: QbitClient,
    contexts: list[AddContext],
    chat_id: int,
) -> None:
    if not contexts:
        return
    task = application.create_task(
        _finalize_added_torrents_batch(
            application,
            qbit,
            contexts,
            chat_id,
        )
    )
    tasks: set[asyncio.Task] = application.bot_data.setdefault("add_finalize_tasks", set())
    tasks.add(task)
    task.add_done_callback(tasks.discard)


async def add_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_allowed_user(update, context):
        return
    if not context.args:
        await update.message.reply_text("用法: /add <一个或多个 magnet/torrent 链接>")
        return
    text = " ".join(context.args).strip()
    links = _extract_torrent_links(text)
    if not links:
        await update.message.reply_text("没有识别到可添加的下载链接。")
        return

    qbit: QbitClient = context.application.bot_data["qbit"]
    chat = update.effective_chat
    if not chat:
        return
    await update.message.reply_text("已收到下载链接，正在提交到 qBittorrent...")
    try:
        result = await _add_torrent_links(context.application, qbit, links)
    except Exception:
        logging.exception("Failed while adding torrent links")
        await update.message.reply_text("提交到 qBittorrent 失败，请稍后重试。")
        return
    await update.message.reply_text(
        _format_add_batch_reply(
            result,
            auto_detected=False,
            settings=context.application.bot_data["settings"],
        ),
        parse_mode=ParseMode.HTML,
    )
    _start_add_background_tasks(context.application, qbit, result.contexts, chat.id)


async def _reply_jellyfin_lookup(
    update: Update, context: ContextTypes.DEFAULT_TYPE, code: str
) -> None:
    jellyfin: JellyfinClient = context.application.bot_data["jellyfin"]
    settings = context.application.bot_data["settings"]
    if not jellyfin.enabled:
        await update.effective_message.reply_text("Jellyfin 查询未启用。")
        return

    items = await jellyfin.find_by_code(code)
    if not items:
        await update.effective_message.reply_text(
            (
                "<b>🔎 Jellyfin 未找到匹配</b>\n"
                f"🏷️ 番号: <code>{escape(code)}</code>"
            ),
            parse_mode=ParseMode.HTML,
        )
        return

    first_item = _pick_best_jellyfin_match(code, items)
    public_base_url = settings.jellyfin_public_base_url or settings.jellyfin_base_url
    caption = _format_jellyfin_caption(
        code,
        first_item,
        len(items),
        public_base_url=public_base_url,
    )
    image_bytes = await jellyfin.get_primary_image_bytes(first_item.item_id)

    if image_bytes:
        await update.effective_message.reply_photo(
            photo=InputFile(io.BytesIO(image_bytes), filename=f"{code}.jpg"),
            caption=caption,
            parse_mode=ParseMode.HTML,
        )
        return

    await update.effective_message.reply_text(caption, parse_mode=ParseMode.HTML)


async def jellyfin_lookup_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    if not await _require_allowed_user(update, context):
        return
    if not context.args:
        await update.message.reply_text("用法: /jav <番号>")
        return
    code = _extract_jav_lookup_code(
        " ".join(context.args),
        _get_jav_pattern(context.application),
    )
    if not code:
        await update.message.reply_text("没有识别到有效番号，例如: /jav PRWF-010")
        return
    await _reply_jellyfin_lookup(update, context, code)


async def text_link_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_allowed_user(update, context):
        return
    message = update.effective_message
    text = message.text if message else None
    if not text:
        return

    links = _extract_torrent_links(text)
    if not links:
        code = _extract_jav_lookup_code(text, _get_jav_pattern(context.application))
        if code:
            await _reply_jellyfin_lookup(update, context, code)
        return

    qbit: QbitClient = context.application.bot_data["qbit"]
    chat = update.effective_chat
    if not chat:
        return
    await message.reply_text("已收到下载链接，正在提交到 qBittorrent...")
    try:
        result = await _add_torrent_links(context.application, qbit, links)
    except Exception:
        logging.exception("Failed while adding torrent links")
        await message.reply_text("提交到 qBittorrent 失败，请稍后重试。")
        return
    await message.reply_text(
        _format_add_batch_reply(
            result,
            auto_detected=True,
            settings=context.application.bot_data["settings"],
        ),
        parse_mode=ParseMode.HTML,
    )
    _start_add_background_tasks(context.application, qbit, result.contexts, chat.id)
