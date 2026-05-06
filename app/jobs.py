from __future__ import annotations

import asyncio
import logging
import time
from enum import Enum
from html import escape

from telegram.constants import ParseMode
from telegram.ext import Application

from app.add_links import AddContext
from app.config import Settings
from app.formatters import _fmt_large_file_threshold, _short_hash
from app.jav_rules import _extract_jav_code, _is_jav_title, _matches_add_context
from app.qbit_client import QbitClient, TorrentSummary
from app.runtime_state import _get_jav_pattern, _get_state, _persist_state


_CONTEXT_POLL_ATTEMPTS = 20
_CONTEXT_POLL_INTERVAL_SECONDS = 1
_FILES_POLL_ATTEMPTS = 10
_JAV_METADATA_POLL_ATTEMPTS = 60
_JAV_METADATA_POLL_INTERVAL_SECONDS = 5


class JavFileSelectionResult(Enum):
    FILTERED = "filtered"
    NO_FILTER_NEEDED = "no_filter_needed"
    NOT_READY = "not_ready"


def _jav_large_file_threshold_bytes(settings: Settings) -> int:
    return int(settings.jav_large_file_threshold_gb * 1024 * 1024 * 1024)


async def _purge_expired_jellyfin_duplicate_codes(application: Application) -> None:
    state = _get_state(application)
    now = int(time.time())
    active = {
        code: expires_at
        for code, expires_at in state.jellyfin_duplicate_codes.items()
        if expires_at > now
    }
    if active != state.jellyfin_duplicate_codes:
        state.jellyfin_duplicate_codes = active
        await _persist_state(application)


async def _handle_jellyfin_duplicate_for_torrent(
    application: Application,
    qbit: QbitClient,
    item: TorrentSummary,
    *,
    chat_id: int,
    is_magnet: bool,
) -> bool:
    settings: Settings = application.bot_data["settings"]
    if not (settings.jellyfin_duplicate_delete_enabled and is_magnet):
        return False

    jellyfin = application.bot_data["jellyfin"]
    if not jellyfin.enabled:
        return False

    code = _extract_jav_code(item.name, _get_jav_pattern(application))
    if not code:
        return False

    await _purge_expired_jellyfin_duplicate_codes(application)
    state = _get_state(application)
    expires_at = state.jellyfin_duplicate_codes.get(code, 0)
    now = int(time.time())
    if expires_at > now:
        await application.bot.send_message(
            chat_id=chat_id,
            text=(
                "<b>ℹ️ Jellyfin 同番号短片已存在</b>\n"
                f"🏷️ 番号: <code>{escape(code)}</code>\n"
                "这次添加发生在 3 小时豁免窗口内，所以不会自动删除，会保留下载。"
            ),
            parse_mode=ParseMode.HTML,
        )
        return False

    jellyfin_items = await jellyfin.find_by_code(code)
    if not jellyfin_items:
        return False

    await qbit.delete_torrent(item.hash, delete_files=False)
    state.jellyfin_duplicate_codes[code] = now + settings.jellyfin_duplicate_grace_hours * 3600
    state.jav_processed_hashes.add(item.hash)
    await _persist_state(application)
    await application.bot.send_message(
        chat_id=chat_id,
        text=(
            "<b>⚠️ Jellyfin 已存在同番号短片</b>\n"
            f"🎬 任务: <b>{escape(item.name)}</b>\n"
            f"🏷️ 番号: <code>{escape(code)}</code>\n"
            f"📚 Jellyfin: <code>{escape(jellyfin_items[0].path or jellyfin_items[0].name)}</code>\n"
            "🗑️ 本次已自动删除 qBittorrent 任务；3 小时内如果你再次添加这个番号，bot 将保留下载，不再自动删除。"
        ),
        parse_mode=ParseMode.HTML,
    )
    return True


async def _apply_jav_category_to_new_torrents(
    application: Application,
    qbit: QbitClient,
    context: AddContext,
    *,
    attempts: int = _CONTEXT_POLL_ATTEMPTS,
    interval_seconds: int = _CONTEXT_POLL_INTERVAL_SECONDS,
) -> list[TorrentSummary]:
    settings: Settings = application.bot_data["settings"]
    pattern = _get_jav_pattern(application)
    processed_hashes = _get_state(application).jav_processed_hashes

    try:
        await qbit.create_category(settings.jav_category_name)
    except Exception:
        pass
    categorized: dict[str, TorrentSummary] = {}

    for _ in range(attempts):
        torrents = await qbit.list_torrents(filter_name="all")
        new_torrents = [
            item
            for item in torrents
            if item.hash not in processed_hashes and _matches_add_context(item, context)
        ]
        matched = [item for item in new_torrents if _is_jav_title(item.name, pattern)]
        for item in matched:
            if item.hash in categorized:
                continue
            await qbit.set_category(item.hash, settings.jav_category_name)
            categorized[item.hash] = item
        if categorized:
            return list(categorized.values())
        await asyncio.sleep(interval_seconds)

    return []


async def _apply_jav_file_selection(
    application: Application,
    qbit: QbitClient,
    torrent_hash: str,
) -> JavFileSelectionResult:
    threshold = _jav_large_file_threshold_bytes(application.bot_data["settings"])
    for _ in range(_FILES_POLL_ATTEMPTS):
        files = await qbit.get_torrent_files(torrent_hash)
        if not files:
            await asyncio.sleep(1)
            continue

        large_files = [item for item in files if item.size > threshold]
        small_files = [item for item in files if item.size <= threshold]
        if not large_files:
            return JavFileSelectionResult.NO_FILTER_NEEDED
        if not small_files:
            return JavFileSelectionResult.NO_FILTER_NEEDED

        await qbit.set_file_priority(torrent_hash, [item.index for item in large_files], 1)
        try:
            await qbit.set_file_priority(torrent_hash, [item.index for item in small_files], 0)
        except Exception:
            logging.exception(
                "Failed to skip small files after enabling large files for torrent %s",
                torrent_hash,
            )
            return JavFileSelectionResult.NOT_READY
        return JavFileSelectionResult.FILTERED

    return JavFileSelectionResult.NOT_READY


async def _notify_completion_loop(application: Application) -> None:
    settings: Settings = application.bot_data["settings"]
    qbit: QbitClient = application.bot_data["qbit"]
    state = _get_state(application)

    while True:
        try:
            torrents = await qbit.list_torrents(filter_name="completed")
            active_hashes = {item.hash for item in torrents}
            stale_hashes = state.notified_completed_hashes - active_hashes
            if stale_hashes:
                state.notified_completed_hashes.difference_update(stale_hashes)
                await _persist_state(application)

            if not application.bot_data.get("completion_monitor_initialized", False):
                state.notified_completed_hashes.update(item.hash for item in torrents)
                application.bot_data["completion_monitor_initialized"] = True
                await _persist_state(application)
                await asyncio.sleep(30)
                continue

            for item in torrents:
                if item.hash in state.notified_completed_hashes:
                    continue
                text = (
                    "<b>✅ 种子下载完成</b>\n"
                    f"📦 <b>{escape(item.name)}</b>\n"
                    f"🔑 <code>{_short_hash(item.hash)}</code>"
                )
                for user_id in settings.telegram_allowed_user_ids:
                    await application.bot.send_message(
                        chat_id=user_id,
                        text=text,
                        parse_mode=ParseMode.HTML,
                    )
                state.notified_completed_hashes.add(item.hash)
                await _persist_state(application)
        except asyncio.CancelledError:
            raise
        except Exception:
            logging.exception("Failed while checking completed torrents")

        await asyncio.sleep(30)


async def _background_finalize_torrent(
    application: Application,
    qbit: QbitClient,
    context: AddContext,
    chat_id: int,
) -> None:
    try:
        settings: Settings = application.bot_data["settings"]
        threshold_text = _fmt_large_file_threshold(settings)
        categorized = await _apply_jav_category_to_new_torrents(application, qbit, context)
        late_metadata_match = False
        if (
            not categorized
            and context.is_magnet
            and context.name_hint
            and _is_jav_title(context.name_hint, _get_jav_pattern(application))
        ):
            categorized = await _apply_jav_category_to_new_torrents(
                application,
                qbit,
                context,
                attempts=_JAV_METADATA_POLL_ATTEMPTS,
                interval_seconds=_JAV_METADATA_POLL_INTERVAL_SECONDS,
            )
            late_metadata_match = bool(categorized)
        if categorized:
            state = _get_state(application)
            filtered_count = 0
            not_ready_count = 0
            retained_categorized: list[TorrentSummary] = []
            for item in categorized:
                if await _handle_jellyfin_duplicate_for_torrent(
                    application,
                    qbit,
                    item,
                    chat_id=chat_id,
                    is_magnet=context.is_magnet,
                ):
                    continue
                state.jav_processed_hashes.add(item.hash)
                retained_categorized.append(item)
                selection_result = await _apply_jav_file_selection(application, qbit, item.hash)
                if selection_result is JavFileSelectionResult.FILTERED:
                    filtered_count += 1
                elif selection_result is JavFileSelectionResult.NOT_READY:
                    not_ready_count += 1
            await _persist_state(application)

            if not retained_categorized:
                return

            if len(retained_categorized) == 1:
                notes = [
                    f"<b>🗂️ 已自动分类到 {escape(settings.jav_category_name)}</b>",
                    "检测到新任务名称包含“多个字母-多个数字”的格式。",
                ]
                if late_metadata_match:
                    notes.append("🧲 已在 magnet 元数据完成后补判并自动归类。")
                if filtered_count:
                    notes.append(f"📁 已仅保留大于 {threshold_text} 的文件下载，小文件已跳过。")
                if not_ready_count:
                    notes.append("⚠️ 文件元数据暂未就绪，尚未完成大小筛选；可以稍后发送 `/retryjav <hash>` 重试。")
            else:
                notes = [
                    f"<b>🗂️ 已自动分类 {len(retained_categorized)} 个任务到 {escape(settings.jav_category_name)}</b>",
                    "检测到这些新任务名称包含“多个字母-多个数字”的格式。",
                ]
                if late_metadata_match:
                    notes.append("🧲 这些任务是在 magnet 元数据完成后补判归类的。")
                if filtered_count:
                    notes.append(
                        f"📁 其中 {filtered_count} 个任务已仅保留大于 {threshold_text} 的文件下载，小文件已跳过。"
                    )
                if not_ready_count:
                    notes.append(
                        f"⚠️ 其中 {not_ready_count} 个任务文件元数据暂未就绪，尚未完成大小筛选；可以稍后用 `/retryjav <hash>` 重试。"
                    )
            await application.bot.send_message(
                chat_id=chat_id,
                text="\n".join(notes),
                parse_mode=ParseMode.HTML,
            )
            return

        if context.name_hint and _is_jav_title(context.name_hint, _get_jav_pattern(application)):
            await application.bot.send_message(
                chat_id=chat_id,
                text=(
                    "<b>⚠️ JAV 自动分类未完成</b>\n"
                    f"目标: <b>{escape(context.name_hint)}</b>\n"
                    "可以稍后发送 `/retryjav <hash>` 重新处理。"
                ),
                parse_mode=ParseMode.HTML,
            )
    except Exception:
        logging.exception("Failed to auto-categorize newly added torrent")
        await application.bot.send_message(
            chat_id=chat_id,
            text=(
                "<b>⚠️ 后台处理失败</b>\n"
                "自动分类或文件筛选没有完成，可以稍后发送 `/retryjav <hash>` 重试。"
            ),
            parse_mode=ParseMode.HTML,
        )
