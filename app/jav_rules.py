from __future__ import annotations

import re
import unicodedata

from app.add_links import AddContext
from app.jav_patterns import DEFAULT_JAV_NAME_REGEX
from app.qbit_client import TorrentSummary


_CONTEXT_LOOKBACK_SECONDS = 10
_JUNK_CODE_PREFIXES = {
    "FHD",
    "HD",
    "HHD",
    "SD",
    "UHD",
    "HDTV",
    "WEBDL",
    "WEBRIP",
    "BLURAY",
    "DVDRIP",
    "XVID",
    "AVC",
    "HEVC",
}


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
    return _extract_jav_code(name, pattern) is not None


def _normalize_search_text(value: str) -> str:
    return unicodedata.normalize("NFKC", value)


def _normalize_extracted_jav_code(value: str) -> str:
    code = _normalize_search_text(value).strip().upper()
    code = re.sub(r"[-_.\s]+", "-", code)
    code = re.sub(r"-+", "-", code).strip("-")

    fc2_match = re.fullmatch(r"FC2(?:-(?:PPV|PPVDB))?-(\d{5,8})", code)
    if fc2_match:
        return f"FC2-PPV-{fc2_match.group(1)}"

    heyzo_match = re.fullmatch(r"HEYZO(?:-HD)?-(\d{3,5})", code)
    if heyzo_match:
        return f"HEYZO-{heyzo_match.group(1)}"

    carib_match = re.fullmatch(r"CARIB(?:BEANCOM)?-(\d{6})-(\d{3})", code)
    if carib_match:
        return f"CARIB-{carib_match.group(1)}-{carib_match.group(2)}"

    tokyo_hot_match = re.fullmatch(r"TOKYO-HOT-N-?(\d{3,5})", code)
    if tokyo_hot_match:
        return f"N-{tokyo_hot_match.group(1)}"

    standard_match = re.fullmatch(r"([A-Z]{2,8})-?(\d{2,5})", code)
    if standard_match:
        return f"{standard_match.group(1)}-{standard_match.group(2)}"

    return code


def _jav_code_score(code: str) -> int:
    if code.startswith(("FC2-", "HEYZO-", "1PONDO-", "CARIB-", "N-")):
        return 100

    standard_match = re.fullmatch(r"([A-Z]{2,8})-(\d{2,5})", code)
    if not standard_match:
        return 0

    prefix, number = standard_match.groups()
    if prefix in _JUNK_CODE_PREFIXES:
        return -100
    if len(number) >= 3:
        return 40
    return 20


def _extract_jav_code(name: str, pattern: re.Pattern[str]) -> str | None:
    candidates = [
        (_jav_code_score(code), match.start(), code)
        for match in pattern.finditer(_normalize_search_text(name))
        for code in [_normalize_extracted_jav_code(match.group(0))]
        if _jav_code_score(code) >= 0
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda item: (-item[0], item[1]))
    return candidates[0][2]


def _extract_jav_lookup_code(text: str, pattern: re.Pattern[str]) -> str | None:
    stripped = text.strip()
    if not stripped or len(stripped) > 80:
        return None
    return _extract_jav_code(stripped, pattern)
