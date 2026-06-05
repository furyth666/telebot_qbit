from __future__ import annotations

import asyncio
import logging
import re
import time
from enum import Enum
from html import escape

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import Application

from app.add_links import AddContext
from app.config import Settings
from app.formatters import _fmt_large_file_threshold, _short_hash
from app.jav_rules import _extract_jav_code, _matches_add_context
from app.qbit_client import QbitClient, TorrentCategory, TorrentSummary
from app.runtime_state import _get_jav_pattern, _get_state, _persist_state


_CONTEXT_POLL_ATTEMPTS = 20
_CONTEXT_POLL_INTERVAL_SECONDS = 1
_FILES_POLL_ATTEMPTS = 10
_CATEGORY_BUTTONS_PER_ROW = 2
_VIDEO_FILE_EXTENSIONS = (".avi", ".m2ts", ".m4v", ".mkv", ".mov", ".mp4", ".ts", ".wmv")
_FOUR_K_PATTERN = re.compile(r"(?i)(?:^|[^a-z0-9])(?:4k|2160p|uhd)(?:$|[^a-z0-9])")


class JavFileSelectionResult(Enum):
    FILTERED = "filtered"
    NO_FILTER_NEEDED = "no_filter_needed"
    NOT_READY = "not_ready"


def _jav_large_file_threshold_bytes(settings: Settings) -> int:
    return int(settings.jav_large_file_threshold_gb * 1024 * 1024 * 1024)


def _looks_like_4k(value: str) -> bool:
    return bool(_FOUR_K_PATTERN.search(value))


def _looks_like_video_file(value: str) -> bool:
    lowered = value.lower()
    return lowered.endswith(_VIDEO_FILE_EXTENSIONS)


async def _torrent_has_4k_video(qbit: QbitClient, item: TorrentSummary) -> bool:
    if _looks_like_4k(item.name):
        return True

    try:
        files = await qbit.get_torrent_files(item.hash)
    except Exception:
        logging.exception("Failed to inspect torrent files for 4K duplicate policy")
        return False

    return any(
        _looks_like_video_file(file.name) and _looks_like_4k(file.name)
        for file in files
    )


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
    code: str,
) -> bool:
    settings: Settings = application.bot_data["settings"]
    jellyfin = application.bot_data["jellyfin"]
    if not jellyfin.enabled:
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

    first_item_path = jellyfin_items[0].path or jellyfin_items[0].name
    torrent_is_4k = await _torrent_has_4k_video(qbit, item)
    jellyfin_has_4k = any(
        _looks_like_4k(jellyfin_item.name) or _looks_like_4k(jellyfin_item.path)
        for jellyfin_item in jellyfin_items
    )
    if torrent_is_4k and not jellyfin_has_4k:
        await application.bot.send_message(
            chat_id=chat_id,
            text=(
                "<b>ℹ️ Jellyfin 已有同番号，但本次是 4K 版本</b>\n"
                f"🎬 任务: <b>{escape(item.name)}</b>\n"
                f"🏷️ 番号: <code>{escape(code)}</code>\n"
                f"📚 Jellyfin: <code>{escape(first_item_path)}</code>\n"
                "库内未识别到 4K 同版本，本次会继续保留下载。"
            ),
            parse_mode=ParseMode.HTML,
        )
        return False

    if not (settings.jellyfin_duplicate_delete_enabled and is_magnet):
        await application.bot.send_message(
            chat_id=chat_id,
            text=(
                "<b>ℹ️ Jellyfin 同番号短片已存在</b>\n"
                f"🎬 任务: <b>{escape(item.name)}</b>\n"
                f"🏷️ 番号: <code>{escape(code)}</code>\n"
                f"📚 Jellyfin: <code>{escape(first_item_path)}</code>\n"
                "当前未启用自动删除，任务会继续保留并按 JAV 处理。"
            ),
            parse_mode=ParseMode.HTML,
        )
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
            f"📚 Jellyfin: <code>{escape(first_item_path)}</code>\n"
            "🗑️ 本次已自动删除 qBittorrent 任务；3 小时内如果你再次添加这个番号，bot 将保留下载，不再自动删除。"
        ),
        parse_mode=ParseMode.HTML,
    )
    return True


async def _find_new_torrents(
    qbit: QbitClient,
    context: AddContext,
    *,
    attempts: int = _CONTEXT_POLL_ATTEMPTS,
    interval_seconds: int = _CONTEXT_POLL_INTERVAL_SECONDS,
) -> list[TorrentSummary]:
    for _ in range(attempts):
        torrents = await qbit.list_torrents(filter_name="all")
        new_torrents = [
            item
            for item in torrents
            if _matches_add_context(item, context)
        ]
        if new_torrents:
            return new_torrents
        await asyncio.sleep(interval_seconds)

    return []


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


async def _send_category_prompt(
    application: Application,
    qbit: QbitClient,
    item: TorrentSummary,
    *,
    chat_id: int,
) -> None:
    categories = await qbit.list_categories()
    choices = _category_choices(categories)
    lock = application.bot_data.get("category_prompt_lock")
    if lock is None:
        lock = asyncio.Lock()
        application.bot_data["category_prompt_lock"] = lock

    async with lock:
        prompted: set[str] = application.bot_data.setdefault(
            "prompted_category_hashes",
            set(),
        )
        if item.hash in prompted:
            return
        pending: dict[str, list[str]] = application.bot_data.setdefault(
            "pending_category_choices",
            {},
        )
        pending[item.hash] = choices
        prompted.add(item.hash)

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


async def _handle_jav_torrent(
    application: Application,
    qbit: QbitClient,
    item: TorrentSummary,
    *,
    chat_id: int,
    is_magnet: bool,
) -> bool:
    settings: Settings = application.bot_data["settings"]
    code = _extract_jav_code(item.name, _get_jav_pattern(application))
    if not code:
        return False

    if await _handle_jellyfin_duplicate_for_torrent(
        application,
        qbit,
        item,
        chat_id=chat_id,
        is_magnet=is_magnet,
        code=code,
    ):
        return True

    try:
        await qbit.create_category(settings.jav_category_name)
    except Exception:
        pass
    await qbit.set_category(item.hash, settings.jav_category_name)
    selection_result = await _apply_jav_file_selection(application, qbit, item.hash)

    state = _get_state(application)
    state.jav_processed_hashes.add(item.hash)
    await _persist_state(application)

    notes = [
        f"<b>已识别 JAV 并移动到 {escape(settings.jav_category_name)}</b>",
        f"📦 <b>{escape(item.name)}</b>",
        f"🏷️ 番号: <code>{escape(code)}</code>",
        f"🔑 <code>{escape(_short_hash(item.hash))}</code>",
    ]
    if selection_result is JavFileSelectionResult.FILTERED:
        notes.append(f"📁 已仅保留大于 {_fmt_large_file_threshold(settings)} 的文件下载，小文件已跳过。")
    elif selection_result is JavFileSelectionResult.NOT_READY:
        notes.append("⚠️ 文件元数据暂未就绪，尚未完成大小筛选；可稍后发送 `/retryjav <hash>`。")

    await application.bot.send_message(
        chat_id=chat_id,
        text="\n".join(notes),
        parse_mode=ParseMode.HTML,
    )
    return True


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
        new_torrents = await _find_new_torrents(qbit, context)
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
                await _send_category_prompt(application, qbit, item, chat_id=chat_id)
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
