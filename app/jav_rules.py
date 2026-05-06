from __future__ import annotations

import re

from app.add_links import AddContext
from app.qbit_client import TorrentSummary


_CONTEXT_LOOKBACK_SECONDS = 10


def _normalize_name_for_match(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _matches_add_context(item: TorrentSummary, context: AddContext) -> bool:
    if item.hash in context.known_hashes:
        return False
    if item.added_on and item.added_on < context.started_at - _CONTEXT_LOOKBACK_SECONDS:
        return False
    if not context.name_hint:
        return True

    normalized_hint = _normalize_name_for_match(context.name_hint)
    normalized_name = _normalize_name_for_match(item.name)
    if not normalized_hint:
        return True
    return normalized_hint in normalized_name or normalized_name in normalized_hint


def _is_jav_title(name: str, pattern: re.Pattern[str]) -> bool:
    return bool(pattern.search(name))


def _extract_jav_code(name: str, pattern: re.Pattern[str]) -> str | None:
    match = pattern.search(name)
    if not match:
        return None
    return match.group(0).upper()


def _extract_jav_lookup_code(text: str, pattern: re.Pattern[str]) -> str | None:
    stripped = text.strip()
    if not stripped or len(stripped) > 80:
        return None
    return _extract_jav_code(stripped, pattern)
