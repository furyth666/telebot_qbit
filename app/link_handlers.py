from __future__ import annotations

import io
from html import escape

from telegram import InputFile, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from app.add_flow import submit_add_links_from_text
from app.add_links import extract_torrent_links
from app.formatters import format_jellyfin_caption, format_stash_caption
from app.handler_utils import require_allowed_user
from app.jav_rules import extract_jav_lookup_code
from app.jellyfin_client import JellyfinClient, JellyfinItem
from app.runtime_state import get_jav_pattern, runtime_context
from app.stash_client import StashClient


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


async def add_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_allowed_user(update, context):
        return
    if not context.args:
        await update.message.reply_text("用法: /add <一个或多个 magnet/torrent 链接>")
        return
    text = " ".join(context.args).strip()
    chat = update.effective_chat
    if not chat:
        return
    result = await submit_add_links_from_text(
        context.application,
        text,
        auto_detected=False,
        chat_id=chat.id,
    )
    if not result:
        await update.message.reply_text("没有识别到可添加的下载链接。")
        return
    await update.message.reply_text(
        result.reply_text,
        parse_mode=ParseMode.HTML,
    )


async def _reply_jellyfin_lookup(
    update: Update, context: ContextTypes.DEFAULT_TYPE, code: str
) -> None:
    runtime = runtime_context(context.application)
    jellyfin: JellyfinClient = runtime.jellyfin
    settings = runtime.settings
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
    caption = format_jellyfin_caption(
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
    if not await require_allowed_user(update, context):
        return
    if not context.args:
        await update.message.reply_text("用法: /jav <番号>")
        return
    code = extract_jav_lookup_code(
        " ".join(context.args),
        get_jav_pattern(context.application),
    )
    if not code:
        await update.message.reply_text("没有识别到有效番号，例如: /jav PRWF-010")
        return
    await _reply_jellyfin_lookup(update, context, code)


async def _reply_stash_lookup(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    query: str,
) -> str:
    runtime = runtime_context(context.application)
    stash: StashClient | None = runtime.data.get("stash")
    settings = runtime.settings
    if not stash or not stash.enabled or not query:
        return "disabled"

    scenes = await stash.find_scenes_by_query(query)
    if not scenes:
        return "not_found"

    caption = format_stash_caption(
        query,
        scenes[0],
        len(scenes),
        base_url=settings.stash_base_url,
    )
    await update.effective_message.reply_text(caption, parse_mode=ParseMode.HTML)
    return "found"


async def text_link_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_allowed_user(update, context):
        return
    message = update.effective_message
    text = message.text if message else None
    if not text:
        return

    chat = update.effective_chat
    links = extract_torrent_links(text)
    if links and not chat:
        return
    if not links:
        code = extract_jav_lookup_code(text, get_jav_pattern(context.application))
        if code:
            await _reply_jellyfin_lookup(update, context, code)
            return
        query = text.strip()
        if await _reply_stash_lookup(update, context, query) == "found":
            return
        await message.reply_text("没有识别到下载链接、有效番号或 Stash 可查询的片名。")
        return

    result = await submit_add_links_from_text(
        context.application,
        text,
        auto_detected=True,
        chat_id=chat.id,
    )
    await message.reply_text(
        result.reply_text,
        parse_mode=ParseMode.HTML,
    )


async def stash_lookup_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_allowed_user(update, context):
        return
    if not context.args:
        await update.message.reply_text("用法: /stash <标题/演员/工作室>")
        return
    query = " ".join(context.args).strip()
    if not query:
        await update.message.reply_text("查询词不能为空。")
        return

    stash_result = await _reply_stash_lookup(update, context, query)
    if stash_result == "disabled":
        await update.effective_message.reply_text("Stash 查询未启用。")
        return
    if stash_result == "not_found":
        await update.effective_message.reply_text(
            (
                "<b>🔎 Stash 未找到匹配</b>\n"
                f"🔎 查询: <code>{escape(query)}</code>"
            ),
            parse_mode=ParseMode.HTML,
        )
