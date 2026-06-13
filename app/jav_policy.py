from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from enum import Enum

from telegram.ext import Application

from app.config import Settings
from app.jellyfin_client import JellyfinItem
from app.qbit_client import QbitClient, TorrentSummary
from app.runtime_state import get_state, persist_state, runtime_context


_FILES_POLL_ATTEMPTS = 10
_VIDEO_FILE_EXTENSIONS = (".avi", ".m2ts", ".m4v", ".mkv", ".mov", ".mp4", ".ts", ".wmv")
_FOUR_K_PATTERN = re.compile(r"(?i)(?:^|[^a-z0-9])(?:4k|2160p|uhd)(?:$|[^a-z0-9])")

__all__ = [
    "JavCategoryResult",
    "JavFileSelectionResult",
    "JellyfinDuplicateResult",
    "JellyfinDuplicateStatus",
    "apply_jav_category_policy",
    "apply_jav_file_selection",
    "handle_jellyfin_duplicate_policy",
    "purge_expired_jellyfin_duplicate_codes",
]


class JavFileSelectionResult(Enum):
    FILTERED = "filtered"
    NO_FILTER_NEEDED = "no_filter_needed"
    NOT_READY = "not_ready"


class JellyfinDuplicateStatus(Enum):
    NONE = "none"
    WITHIN_GRACE = "within_grace"
    FOUND_KEEP = "found_keep"
    FOUR_K_EXCEPTION = "four_k_exception"
    DELETED = "deleted"


@dataclass(frozen=True)
class JavCategoryResult:
    torrent_hash: str
    category: str
    selection_result: JavFileSelectionResult


@dataclass(frozen=True)
class JellyfinDuplicateResult:
    status: JellyfinDuplicateStatus
    code: str
    first_item_path: str = ""

    @property
    def handled(self) -> bool:
        return self.status is JellyfinDuplicateStatus.DELETED


def _jav_large_file_threshold_bytes(settings: Settings) -> int:
    return int(settings.jav_large_file_threshold_gb * 1024 * 1024 * 1024)


def _looks_like_4k(value: str) -> bool:
    return bool(_FOUR_K_PATTERN.search(value))


def _looks_like_video_file(value: str) -> bool:
    return value.lower().endswith(_VIDEO_FILE_EXTENSIONS)


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


async def purge_expired_jellyfin_duplicate_codes(application: Application) -> None:
    state = get_state(application)
    now = int(time.time())
    active = {
        code: expires_at
        for code, expires_at in state.jellyfin_duplicate_codes.items()
        if expires_at > now
    }
    if active != state.jellyfin_duplicate_codes:
        state.jellyfin_duplicate_codes = active
        await persist_state(application)


def _first_item_path(items: list[JellyfinItem]) -> str:
    return items[0].path or items[0].name


async def handle_jellyfin_duplicate_policy(
    application: Application,
    qbit: QbitClient,
    item: TorrentSummary,
    *,
    is_magnet: bool,
    code: str,
) -> JellyfinDuplicateResult:
    context = runtime_context(application)
    settings = context.settings
    jellyfin = context.jellyfin
    if not jellyfin.enabled:
        return JellyfinDuplicateResult(JellyfinDuplicateStatus.NONE, code)

    await purge_expired_jellyfin_duplicate_codes(application)
    state = get_state(application)
    now = int(time.time())
    expires_at = state.jellyfin_duplicate_codes.get(code, 0)
    if expires_at > now:
        return JellyfinDuplicateResult(JellyfinDuplicateStatus.WITHIN_GRACE, code)

    jellyfin_items = await jellyfin.find_by_code(code)
    if not jellyfin_items:
        return JellyfinDuplicateResult(JellyfinDuplicateStatus.NONE, code)

    first_item_path = _first_item_path(jellyfin_items)
    torrent_is_4k = await _torrent_has_4k_video(qbit, item)
    jellyfin_has_4k = any(
        _looks_like_4k(jellyfin_item.name) or _looks_like_4k(jellyfin_item.path)
        for jellyfin_item in jellyfin_items
    )
    if torrent_is_4k and not jellyfin_has_4k:
        return JellyfinDuplicateResult(
            JellyfinDuplicateStatus.FOUR_K_EXCEPTION,
            code,
            first_item_path,
        )

    if not (settings.jellyfin_duplicate_delete_enabled and is_magnet):
        return JellyfinDuplicateResult(
            JellyfinDuplicateStatus.FOUND_KEEP,
            code,
            first_item_path,
        )

    await qbit.delete_torrent(item.hash, delete_files=False)
    state.jellyfin_duplicate_codes[code] = (
        now + settings.jellyfin_duplicate_grace_hours * 3600
    )
    state.jav_processed_hashes.add(item.hash)
    await persist_state(application)
    return JellyfinDuplicateResult(
        JellyfinDuplicateStatus.DELETED,
        code,
        first_item_path,
    )


async def apply_jav_file_selection(
    application: Application,
    qbit: QbitClient,
    torrent_hash: str,
) -> JavFileSelectionResult:
    threshold = _jav_large_file_threshold_bytes(runtime_context(application).settings)
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


async def apply_jav_category_policy(
    application: Application,
    qbit: QbitClient,
    torrent_hash: str,
) -> JavCategoryResult:
    settings = runtime_context(application).settings
    try:
        await qbit.create_category(settings.jav_category_name)
    except Exception:
        pass
    await qbit.set_category(torrent_hash, settings.jav_category_name)
    selection_result = await apply_jav_file_selection(application, qbit, torrent_hash)

    state = get_state(application)
    state.jav_processed_hashes.add(torrent_hash)
    await persist_state(application)

    return JavCategoryResult(
        torrent_hash=torrent_hash,
        category=settings.jav_category_name,
        selection_result=selection_result,
    )
