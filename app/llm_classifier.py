from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from app.config import Settings
from app.qbit_client import TorrentCategory, TorrentFile, TorrentSummary

__all__ = [
    "AvMetadata",
    "LlmCategoryDecision",
    "classify_torrent",
    "extract_av_metadata",
]


_SYSTEM_PROMPT = """You classify newly added qBittorrent torrents.
Return JSON only with category, confidence, and reason.
The category must be exactly one of the provided category names.
Use an empty string only when the torrent should remain uncategorized.
Never invent a category."""

def _category_guidance(
    settings: Settings,
    *,
    jav_prefixes: list[str] | None = None,
) -> str:
    jav_category = settings.jav_category_name.strip() or "JAV"
    guidance = f"""Local category policy:
- Use {jav_category} for Japanese adult releases when that category is available: product-code releases, Japanese performer collections, and torrents whose file list contains Japanese product codes.
- The configured JAV title/product-code rule is the source of truth: {settings.jav_name_regex}
- Treat matches to that configured rule as JAV product-code evidence; do not rely on a fixed vendor-prefix list.
- Use {jav_category} for Japanese performer collection names such as Mikami Yua / Yua Mikami, even when the torrent name says BluRay, Collection, ISO, or AV.
- The text AV inside a filename is not enough to choose the AV category when the release is Japanese/JAV-related.
- Use AV for Western adult video releases, studio-title releases, and generic XXX adult videos without Japanese product-code naming or Japanese performer context.
- Source/site markers such as JAVDB, JAVDB.com, and javdb.com are ignored before classification.
- Use TV only for television series, anime series, episodes, seasons, or variety shows.
- Use the category names exactly as provided."""
    if jav_prefixes:
        guidance += (
            "\n- Jellyfin currently contains JAV product-code prefixes extracted "
            f"from the media library: {', '.join(jav_prefixes)}."
        )
    return guidance

_SOURCE_MARKER_PATTERN = re.compile(
    r"(?i)(?:[\s._\-\[\](){}]+)?javdb(?:\.com)?(?:[\s._\-\[\](){}]+)?"
)


@dataclass(frozen=True)
class LlmCategoryDecision:
    category: str
    confidence: float
    reason: str


@dataclass(frozen=True)
class AvMetadata:
    title: str
    performers: tuple[str, ...]
    studio: str
    year: str
    search_query: str


def _strip_source_markers(value: str) -> str:
    cleaned = _SOURCE_MARKER_PATTERN.sub(" ", value)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip(" \t\r\n-_.[](){}")


def _file_payload(files: list[TorrentFile], *, limit: int = 30) -> list[dict[str, Any]]:
    return [
        {
            "name": _strip_source_markers(item.name),
            "size_bytes": item.size,
            "priority": item.priority,
        }
        for item in files[:limit]
    ]


def _decision_from_payload(payload: dict[str, Any]) -> LlmCategoryDecision:
    category = str(payload.get("category", ""))
    reason = str(payload.get("reason", "")).strip()
    try:
        confidence = float(payload.get("confidence", 0))
    except (TypeError, ValueError):
        confidence = 0
    confidence = max(0.0, min(confidence, 1.0))
    return LlmCategoryDecision(
        category=category,
        confidence=confidence,
        reason=reason,
    )


def _message_content(response_payload: dict[str, Any]) -> str:
    choices = response_payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError("LLM response did not include choices")
    message = choices[0].get("message")
    if not isinstance(message, dict):
        raise ValueError("LLM response did not include a message")
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise ValueError("LLM response did not include content")
    return content


def _ollama_native_base_url(base_url: str) -> str | None:
    parts = urlsplit(base_url.rstrip("/"))
    if parts.path != "/v1":
        return None
    if parts.hostname not in {"127.0.0.1", "localhost", "0.0.0.0"}:
        return None
    return urlunsplit((parts.scheme, parts.netloc, "", "", ""))


async def _chat_completion(
    settings: Settings,
    messages: list[dict[str, Any]],
    *,
    json_response: bool = True,
) -> dict[str, Any]:
    native_base_url = _ollama_native_base_url(settings.llm_api_base_url)
    if native_base_url is not None:
        request_payload: dict[str, Any] = {
            "model": settings.llm_model,
            "messages": messages,
            "format": "json" if json_response else "",
            "stream": False,
            "think": False,
            "options": {"temperature": 0},
        }
        async with httpx.AsyncClient(
            base_url=native_base_url,
            timeout=settings.llm_request_timeout_seconds,
            trust_env=False,
        ) as client:
            response = await client.post("/api/chat", json=request_payload)
            response.raise_for_status()
        return response.json()

    request_payload = {
        "model": settings.llm_model,
        "messages": messages,
        "response_format": {"type": "json_object"} if json_response else {"type": "text"},
        "temperature": 0,
        "think": False,
    }
    headers = {
        "Authorization": f"Bearer {settings.llm_api_key}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(
        base_url=settings.llm_api_base_url,
        timeout=settings.llm_request_timeout_seconds,
        trust_env=False,
    ) as client:
        response = await client.post(
            "/chat/completions",
            json=request_payload,
            headers=headers,
        )
        response.raise_for_status()
    return response.json()


def _content_from_completion_payload(payload: dict[str, Any]) -> str:
    native_message = payload.get("message")
    if isinstance(native_message, dict):
        content = native_message.get("content")
        if isinstance(content, str) and content.strip():
            return content
        raise ValueError("LLM response did not include content")
    return _message_content(payload)


async def classify_torrent(
    settings: Settings,
    item: TorrentSummary,
    files: list[TorrentFile],
    categories: list[TorrentCategory],
    *,
    jav_prefixes: list[str] | None = None,
) -> LlmCategoryDecision:
    """Classify a torrent, propagating transport, HTTP, and JSON parse errors."""
    category_names = ["", *[category.name for category in categories if category.name]]
    user_payload = {
        "torrent": {
            "name": _strip_source_markers(item.name),
            "hash": item.hash,
            "current_category": item.category,
            "size_bytes": item.size,
        },
        "available_categories": category_names,
        "files": _file_payload(files),
    }
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "system", "content": _category_guidance(settings, jav_prefixes=jav_prefixes)},
        {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
    ]
    response_payload = await _chat_completion(settings, messages)
    content = _content_from_completion_payload(response_payload)
    return _decision_from_payload(json.loads(content))


_AV_METADATA_SYSTEM_PROMPT = """You extract adult video metadata from a torrent for searching a media library.
Return JSON only with these fields:
- title: the main scene or movie title
- performers: list of performer names (empty list if unknown)
- studio: studio or production company name (empty string if unknown)
- year: release year as a string (empty string if unknown)
- search_query: the best concise search query to find this scene in Stash
Do not include explanations."""


def _av_metadata_from_payload(payload: dict[str, Any]) -> AvMetadata:
    title = str(payload.get("title", "")).strip()
    studio = str(payload.get("studio", "")).strip()
    year = str(payload.get("year", "")).strip()
    search_query = str(payload.get("search_query", "")).strip()
    if not search_query and title:
        search_query = title
    performers_value = payload.get("performers")
    performers: tuple[str, ...] = ()
    if isinstance(performers_value, list):
        performers = tuple(
            str(performer).strip()
            for performer in performers_value
            if str(performer).strip()
        )
    return AvMetadata(
        title=title,
        performers=performers,
        studio=studio,
        year=year,
        search_query=search_query,
    )


async def extract_av_metadata(
    settings: Settings,
    item: TorrentSummary,
    files: list[TorrentFile],
) -> AvMetadata:
    """Extract AV metadata for Stash search.

    Propagates transport, HTTP, and JSON parse errors so callers can decide
    whether to fall back to a plain title search.
    """
    user_payload = {
        "torrent_name": _strip_source_markers(item.name),
        "files": _file_payload(files),
    }
    messages = [
        {"role": "system", "content": _AV_METADATA_SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
    ]
    response_payload = await _chat_completion(settings, messages)
    content = _content_from_completion_payload(response_payload)
    return _av_metadata_from_payload(json.loads(content))
