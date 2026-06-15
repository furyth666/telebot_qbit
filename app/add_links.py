from __future__ import annotations

import logging
import re
import time
from base64 import b32decode
from binascii import Error as BinasciiError
from dataclasses import dataclass
from html import escape
from urllib.parse import parse_qs, unquote, urlparse

import httpx
from telegram.ext import Application

from app.add_types import AddBatchResult, AddContext
from app.config import Settings
from app.qbit_client import QbitClient
from app.runtime_state import runtime_context


_URL_PATTERN = re.compile(r"(magnet:\?[^\s,，;；|]+|https?://[^\s,，;；|]+)", re.IGNORECASE)
_LINK_ONLY_SEPARATOR_PATTERN = re.compile(r"[\s,，;；|]+")
_DIRECT_DOWNLOAD_HINTS = (
    ".torrent",
    "/api/rss/dlv2",
    "download.php",
)
_KNOWN_HASH_CACHE_TTL_SECONDS = 10


@dataclass(frozen=True)
class AddTorrentResult:
    is_magnet: bool
    torrent_hash: str | None
    context: AddContext


def _with_expected_hashes(
    context: AddContext,
    expected_hashes: set[str],
) -> AddContext:
    return AddContext(
        known_hashes=set(context.known_hashes),
        started_at=context.started_at,
        name_hint=context.name_hint,
        is_magnet=context.is_magnet,
        expected_hashes=set(expected_hashes),
    )


def _magnet_upload_limit_bytes(settings: Settings) -> int:
    return settings.magnet_upload_limit_kib * 1024


def _extract_links(text: str) -> list[str]:
    seen: set[str] = set()
    links: list[str] = []
    for match in _URL_PATTERN.findall(text):
        candidate = match.strip().strip("<>\"'(),")
        if candidate and candidate not in seen:
            seen.add(candidate)
            links.append(candidate)
    return links


def extract_torrent_links(text: str) -> list[str]:
    links = _extract_links(text)
    if not links:
        return []

    candidate_links = [link for link in links if _looks_like_torrent_link(link)]
    if not candidate_links and _text_is_link_only(text, links):
        candidate_links = links
    return candidate_links


def _looks_like_torrent_link(link: str) -> bool:
    lowered = link.lower()
    if lowered.startswith("magnet:?"):
        return True
    parsed = urlparse(lowered)
    path = parsed.path
    basename = path.rsplit("/", 1)[-1]
    if basename.endswith(".torrent") or basename == "download.php":
        return True
    if path.rstrip("/") == "/download" or "/api/rss/dlv2" in path:
        return True
    return any(hint in lowered for hint in _DIRECT_DOWNLOAD_HINTS)


def _text_is_link_only(text: str, links: list[str]) -> bool:
    remainder = text
    for link in links:
        remainder = remainder.replace(link, " ")
    remainder = _LINK_ONLY_SEPARATOR_PATTERN.sub("", remainder)
    return not remainder


def _extract_name_hint(url: str) -> str | None:
    if url.lower().startswith("magnet:?"):
        query = parse_qs(urlparse(url).query)
        raw = query.get("dn", [])
        if raw and raw[0]:
            return unquote(raw[0])
        return None

    parsed = urlparse(url)
    path = parsed.path.rsplit("/", 1)[-1]
    if path:
        return unquote(path)
    return None


def _extract_magnet_hash(url: str) -> str | None:
    if not url.lower().startswith("magnet:?"):
        return None

    query = parse_qs(urlparse(url).query)
    for value in query.get("xt", []):
        prefix = "urn:btih:"
        if not value.lower().startswith(prefix):
            continue

        raw_hash = unquote(value[len(prefix) :]).strip()
        if re.fullmatch(r"[A-Fa-f0-9]{40}", raw_hash):
            return raw_hash.lower()
        if re.fullmatch(r"[A-Za-z2-7]{32}", raw_hash):
            try:
                return b32decode(raw_hash.upper()).hex()
            except (BinasciiError, ValueError):
                return None
    return None


async def _add_torrent_url(
    application: Application,
    qbit: QbitClient,
    url: str,
    known_hashes: set[str],
) -> AddTorrentResult:
    settings: Settings = runtime_context(application).settings
    is_magnet = url.lower().startswith("magnet:?")
    upload_limit = (
        _magnet_upload_limit_bytes(settings) if is_magnet else None
    )
    await qbit.add_torrent_url_with_options(url, upload_limit=upload_limit)
    return AddTorrentResult(
        is_magnet=is_magnet,
        torrent_hash=_extract_magnet_hash(url),
        context=AddContext(
            known_hashes=set(known_hashes),
            started_at=int(time.time()),
            name_hint=_extract_name_hint(url) if is_magnet else None,
            is_magnet=is_magnet,
        ),
    )


def _format_add_failure(index: int, error: Exception) -> str:
    if isinstance(error, httpx.HTTPStatusError):
        reason = f"qBittorrent 返回 {error.response.status_code}"
    elif isinstance(error, RuntimeError):
        reason = str(error)
    else:
        reason = error.__class__.__name__
    return f"第 {index} 条: {escape(reason)}"


async def add_torrent_links(
    application: Application,
    qbit: QbitClient,
    links: list[str],
) -> AddBatchResult:
    context = runtime_context(application)
    async with context.add_submission_lock():
        magnet_count = 0
        contexts: list[AddContext] = []
        failures: list[str] = []
        known_hashes = await _get_cached_known_hashes(application, qbit)

        for index, link in enumerate(links, start=1):
            try:
                result = await _add_torrent_url(application, qbit, link, known_hashes)
            except Exception as exc:
                failure = _format_add_failure(index, exc)
                logging.warning("Failed to add torrent link: %s", failure)
                failures.append(failure)
                continue

            if result.is_magnet:
                magnet_count += 1
            if result.torrent_hash:
                known_hashes.add(result.torrent_hash)
                result = AddTorrentResult(
                    is_magnet=result.is_magnet,
                    torrent_hash=result.torrent_hash,
                    context=_with_expected_hashes(
                        result.context,
                        {result.torrent_hash},
                    ),
                )
            else:
                try:
                    refreshed_hashes = await _refresh_known_hashes(qbit)
                except Exception:
                    logging.exception("Failed to refresh qBittorrent hashes after add")
                else:
                    expected_hashes = refreshed_hashes - known_hashes
                    if expected_hashes:
                        result = AddTorrentResult(
                            is_magnet=result.is_magnet,
                            torrent_hash=result.torrent_hash,
                            context=_with_expected_hashes(
                                result.context,
                                expected_hashes,
                            ),
                        )
                    known_hashes = refreshed_hashes
            contexts.append(result.context)

        _set_cached_known_hashes(application, known_hashes)
    return AddBatchResult(
        total_links=len(links),
        success_count=len(contexts),
        magnet_count=magnet_count,
        contexts=contexts,
        failures=failures,
    )


def format_add_batch_reply(
    result: AddBatchResult,
    *,
    auto_detected: bool,
    settings: Settings,
) -> str:
    if result.total_links == 1 and result.success_count == 1:
        if auto_detected:
            notes = ["<b>➕ 已自动识别并添加下载链接</b>"]
        else:
            notes = ["<b>➕ 已提交添加请求</b>"]
        if result.magnet_count == 1:
            notes.append(
                f"📤 该 magnet 任务上传限速已设为 {settings.magnet_upload_limit_kib} KB/s"
            )
        return "\n".join(notes)

    notes: list[str] = []
    if result.success_count:
        if result.failures:
            notes.append(
                f"<b>➕ 已添加 {result.success_count} 个下载链接，失败 {len(result.failures)} 个</b>"
            )
        else:
            notes.append(f"<b>➕ 已添加 {result.success_count} 个下载链接</b>")
        if result.magnet_count:
            notes.append(
                f"📤 其中 {result.magnet_count} 个 magnet 任务上传限速已设为 "
                f"{settings.magnet_upload_limit_kib} KB/s"
            )
    else:
        notes.append(f"<b>❌ {result.total_links} 个下载链接全部添加失败</b>")

    if result.failures:
        notes.append("失败摘要:")
        notes.extend(f"• {failure}" for failure in result.failures[:5])
        if len(result.failures) > 5:
            notes.append(f"• 还有 {len(result.failures) - 5} 个失败项未显示")
    return "\n".join(notes)


async def _get_cached_known_hashes(
    application: Application,
    qbit: QbitClient,
) -> set[str]:
    context = runtime_context(application)
    cached_hashes = context.get_known_hashes_cache(
        ttl_seconds=_KNOWN_HASH_CACHE_TTL_SECONDS,
    )
    if cached_hashes is not None:
        return cached_hashes

    known_hashes = {item.hash for item in await qbit.list_torrents(filter_name="all")}
    _set_cached_known_hashes(application, known_hashes)
    return known_hashes


async def _refresh_known_hashes(qbit: QbitClient) -> set[str]:
    return {item.hash for item in await qbit.list_torrents(filter_name="all")}


def _set_cached_known_hashes(application: Application, known_hashes: set[str]) -> None:
    runtime_context(application).set_known_hashes_cache(known_hashes)
