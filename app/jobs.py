from __future__ import annotations

import asyncio
import logging
from html import escape

from telegram.constants import ParseMode
from telegram.ext import Application

from app.add_links import AddContext
from app.category_flow import handle_llm_category_torrent, send_category_prompt
from app.config import Settings
from app.formatters import format_large_file_threshold, short_hash
from app.jav_policy import (
    JellyfinDuplicateResult,
    JellyfinDuplicateStatus,
    JavFileSelectionResult,
    apply_jav_category_policy,
    apply_jav_file_selection,
    handle_jellyfin_duplicate_policy,
)
from app.jav_rules import extract_jav_code, matches_add_context
from app.qbit_client import QbitClient, TorrentSummary
from app.runtime_state import get_jav_pattern, get_state, persist_state, runtime_context


async def _send_jellyfin_duplicate_message(
    application: Application,
    item: TorrentSummary,
    *,
    chat_id: int,
    result: JellyfinDuplicateResult,
) -> bool:
    if result.status is JellyfinDuplicateStatus.NONE:
        return False

    if result.status is JellyfinDuplicateStatus.WITHIN_GRACE:
        await application.bot.send_message(
            chat_id=chat_id,
            text=(
                "<b>ℹ️ Jellyfin 同番号短片已存在</b>\n"
                f"🏷️ 番号: <code>{escape(result.code)}</code>\n"
                "这次添加发生在 3 小时豁免窗口内，所以不会自动删除，会保留下载。"
            ),
            parse_mode=ParseMode.HTML,
        )
        return False

    if result.status is JellyfinDuplicateStatus.FOUR_K_EXCEPTION:
        await application.bot.send_message(
            chat_id=chat_id,
            text=(
                "<b>ℹ️ Jellyfin 已有同番号，但本次是 4K 版本</b>\n"
                f"🎬 任务: <b>{escape(item.name)}</b>\n"
                f"🏷️ 番号: <code>{escape(result.code)}</code>\n"
                f"📚 Jellyfin: <code>{escape(result.first_item_path)}</code>\n"
                "库内未识别到 4K 同版本，本次会继续保留下载。"
            ),
            parse_mode=ParseMode.HTML,
        )
        return False

    if result.status is JellyfinDuplicateStatus.FOUND_KEEP:
        await application.bot.send_message(
            chat_id=chat_id,
            text=(
                "<b>ℹ️ Jellyfin 同番号短片已存在</b>\n"
                f"🎬 任务: <b>{escape(item.name)}</b>\n"
                f"🏷️ 番号: <code>{escape(result.code)}</code>\n"
                f"📚 Jellyfin: <code>{escape(result.first_item_path)}</code>\n"
                "当前未启用自动删除，任务会继续保留并按 JAV 处理。"
            ),
            parse_mode=ParseMode.HTML,
        )
        return False

    if result.status is JellyfinDuplicateStatus.DELETED:
        await application.bot.send_message(
            chat_id=chat_id,
            text=(
                "<b>⚠️ Jellyfin 已存在同番号短片</b>\n"
                f"🎬 任务: <b>{escape(item.name)}</b>\n"
                f"🏷️ 番号: <code>{escape(result.code)}</code>\n"
                f"📚 Jellyfin: <code>{escape(result.first_item_path)}</code>\n"
                "🗑️ 本次已自动删除 qBittorrent 任务；3 小时内如果你再次添加这个番号，bot 将保留下载，不再自动删除。"
            ),
            parse_mode=ParseMode.HTML,
        )
        return True

    return False


async def _find_new_torrents(
    qbit: QbitClient,
    context: AddContext,
    *,
    attempts: int,
    interval_seconds: float,
) -> list[TorrentSummary]:
    for _ in range(attempts):
        torrents = await qbit.list_torrents(filter_name="all")
        new_torrents = [
            item
            for item in torrents
            if matches_add_context(item, context)
        ]
        if new_torrents:
            return new_torrents
        await asyncio.sleep(interval_seconds)

    return []


async def _handle_jav_torrent(
    application: Application,
    qbit: QbitClient,
    item: TorrentSummary,
    *,
    chat_id: int,
    is_magnet: bool,
) -> bool:
    if item.hash in get_state(application).jav_processed_hashes:
        return True

    settings: Settings = runtime_context(application).settings
    code = extract_jav_code(item.name, get_jav_pattern(application))
    if not code:
        return False

    duplicate_result = await handle_jellyfin_duplicate_policy(
        application,
        qbit,
        item,
        is_magnet=is_magnet,
        code=code,
    )
    if await _send_jellyfin_duplicate_message(
        application,
        item,
        chat_id=chat_id,
        result=duplicate_result,
    ):
        return True

    result = await apply_jav_category_policy(application, qbit, item.hash)

    notes = [
        f"<b>已识别 JAV 并移动到 {escape(result.category)}</b>",
        f"📦 <b>{escape(item.name)}</b>",
        f"🏷️ 番号: <code>{escape(code)}</code>",
        f"🔑 <code>{escape(short_hash(item.hash))}</code>",
    ]
    if result.selection_result is JavFileSelectionResult.FILTERED:
        notes.append(f"📁 已仅保留大于 {format_large_file_threshold(settings)} 的文件下载，小文件已跳过。")
    elif result.selection_result is JavFileSelectionResult.NOT_READY:
        notes.append("⚠️ 文件元数据暂未就绪，尚未完成大小筛选；可稍后发送 <code>/retryjav &lt;hash&gt;</code>。")

    await application.bot.send_message(
        chat_id=chat_id,
        text="\n".join(notes),
        parse_mode=ParseMode.HTML,
    )
    return True


async def notify_completion_loop(application: Application) -> None:
    context = runtime_context(application)
    settings: Settings = context.settings
    qbit: QbitClient = context.qbit
    state = get_state(application)

    while True:
        try:
            torrents = await qbit.list_torrents(filter_name="completed")
            active_hashes = {item.hash for item in torrents}
            stale_hashes = state.notified_completed_hashes - active_hashes
            if stale_hashes:
                state.notified_completed_hashes.difference_update(stale_hashes)
                await persist_state(application)

            if not context.completion_monitor_initialized:
                state.notified_completed_hashes.update(item.hash for item in torrents)
                context.completion_monitor_initialized = True
                await persist_state(application)
                await asyncio.sleep(30)
                continue

            for item in torrents:
                if item.hash in state.notified_completed_hashes:
                    continue
                text = (
                    "<b>✅ 种子下载完成</b>\n"
                    f"📦 <b>{escape(item.name)}</b>\n"
                    f"🔑 <code>{short_hash(item.hash)}</code>"
                )
                for user_id in settings.telegram_allowed_user_ids:
                    await application.bot.send_message(
                        chat_id=user_id,
                        text=text,
                        parse_mode=ParseMode.HTML,
                    )
                state.notified_completed_hashes.add(item.hash)
                await persist_state(application)
        except asyncio.CancelledError:
            raise
        except Exception:
            logging.exception("Failed while checking completed torrents")

        await asyncio.sleep(30)


async def background_finalize_torrent(
    application: Application,
    qbit: QbitClient,
    context: AddContext,
    chat_id: int,
) -> None:
    try:
        settings: Settings = runtime_context(application).settings
        new_torrents = await _find_new_torrents(
            qbit,
            context,
            attempts=settings.add_context_poll_attempts,
            interval_seconds=settings.add_context_poll_interval_seconds,
        )
        if new_torrents:
            for item in new_torrents:
                if await _handle_jav_torrent(
                    application,
                    qbit,
                    item,
                    chat_id=chat_id,
                    is_magnet=context.is_magnet,
                ):
                    continue
                if await handle_llm_category_torrent(
                    application,
                    qbit,
                    item,
                    chat_id=chat_id,
                ):
                    continue
                await send_category_prompt(application, qbit, item, chat_id=chat_id)
            return

        if context.name_hint:
            await application.bot.send_message(
                chat_id=chat_id,
                text=(
                    "<b>⚠️ 暂时没有定位到新任务</b>\n"
                    f"目标: <b>{escape(context.name_hint)}</b>\n"
                    "可以稍后在任务列表里手动调整分类。"
                ),
                parse_mode=ParseMode.HTML,
            )
    except Exception:
        logging.exception("Failed to prompt category for newly added torrent")
        await application.bot.send_message(
            chat_id=chat_id,
            text=(
                "<b>⚠️ 后台处理失败</b>\n"
                "没有完成分类选择提示，可以稍后在任务列表里手动调整分类。"
            ),
            parse_mode=ParseMode.HTML,
        )
