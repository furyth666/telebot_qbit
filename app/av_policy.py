from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from enum import Enum

from telegram.ext import Application

from app.config import Settings
from app.llm_classifier import AvMetadata, extract_av_metadata
from app.qbit_client import TorrentFile, TorrentSummary
from app.runtime_state import runtime_context
from app.stash_client import StashClient, StashScene

__all__ = [
    "StashDuplicateResult",
    "StashDuplicateStatus",
    "extract_av_search_query",
    "handle_stash_duplicate_policy",
]


class StashDuplicateStatus(Enum):
    NONE = "none"
    FOUND_KEEP = "found_keep"
    UPGRADE_KEEP = "upgrade_keep"


@dataclass(frozen=True)
class StashDuplicateResult:
    status: StashDuplicateStatus
    query: str
    first_scene: StashScene | None = None


_SOURCE_MARKER_PATTERN = re.compile(
    r"(?i)(?:[\s._\-\[\](){}]+)?javdb(?:\.com)?(?:[\s._\-\[\](){}]+)?"
)


def _strip_source_markers(value: str) -> str:
    cleaned = _SOURCE_MARKER_PATTERN.sub(" ", value)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip(" \t\r\n-_.[](){}")


def _fallback_search_query(name: str) -> str:
    return _strip_source_markers(name)


def _search_query_from_metadata(metadata: AvMetadata) -> str:
    if metadata.search_query:
        return metadata.search_query
    if metadata.title:
        parts = [metadata.title]
        if metadata.performers:
            parts.extend(metadata.performers)
        if metadata.studio:
            parts.append(metadata.studio)
        return " ".join(parts)
    return ""


_RESOLUTION_PATTERN = re.compile(
    r"(?i)(?:^|[^a-z0-9])(4k|2160p|uhd|1080p|fhd|720p|hd|480p|sd)(?:$|[^a-z0-9])"
)

_RESOLUTION_RANKS: dict[str, int] = {
    "4k": 4,
    "2160p": 4,
    "uhd": 4,
    "1080p": 3,
    "fhd": 3,
    "720p": 2,
    "hd": 2,
    "480p": 1,
    "sd": 1,
}


def _extract_resolution_rank(value: str) -> int | None:
    best: int | None = None
    for match in _RESOLUTION_PATTERN.finditer(value):
        rank = _RESOLUTION_RANKS.get(match.group(1).lower())
        if rank is not None and (best is None or rank > best):
            best = rank
    return best


def _torrent_resolution_rank(item: TorrentSummary, files: list[TorrentFile]) -> int | None:
    rank = _extract_resolution_rank(item.name)
    if rank is not None:
        return rank
    for file in files:
        rank = _extract_resolution_rank(file.name)
        if rank is not None:
            return rank
    return None


def _scene_resolution_rank(scene: StashScene) -> int | None:
    rank = _extract_resolution_rank(scene.title)
    if rank is not None:
        return rank
    for path in scene.paths:
        rank = _extract_resolution_rank(path)
        if rank is not None:
            return rank
    return None


async def extract_av_search_query(
    settings: Settings,
    item: TorrentSummary,
    files: list[TorrentFile],
) -> str:
    """Try LLM metadata extraction; fall back to cleaned torrent name."""
    if not settings.llm_classify_enabled:
        return _fallback_search_query(item.name)

    try:
        metadata = await extract_av_metadata(settings, item, files)
    except Exception:
        logging.exception("Failed to extract AV metadata for Stash search")
        return _fallback_search_query(item.name)

    query = _search_query_from_metadata(metadata)
    return query or _fallback_search_query(item.name)


async def handle_stash_duplicate_policy(
    application: Application,
    item: TorrentSummary,
    *,
    files: list[TorrentFile],
) -> StashDuplicateResult:
    context = runtime_context(application)
    settings: Settings = context.settings
    stash: StashClient = context.stash
    if not stash.enabled:
        return StashDuplicateResult(StashDuplicateStatus.NONE, "")

    query = await extract_av_search_query(settings, item, files)
    if not query:
        return StashDuplicateResult(StashDuplicateStatus.NONE, "")

    try:
        scenes = await stash.find_scenes_by_query(query)
    except Exception:
        logging.exception("Failed to query Stash for AV duplicate check")
        return StashDuplicateResult(StashDuplicateStatus.NONE, query)

    if not scenes:
        return StashDuplicateResult(StashDuplicateStatus.NONE, query)

    first_scene = scenes[0]
    torrent_rank = _torrent_resolution_rank(item, files)
    stash_max_rank = max(
        (rank for scene in scenes if (rank := _scene_resolution_rank(scene)) is not None),
        default=None,
    )
    if torrent_rank is not None and stash_max_rank is not None and torrent_rank > stash_max_rank:
        return StashDuplicateResult(
            StashDuplicateStatus.UPGRADE_KEEP,
            query,
            first_scene,
        )

    return StashDuplicateResult(
        StashDuplicateStatus.FOUND_KEEP,
        query,
        first_scene,
    )
