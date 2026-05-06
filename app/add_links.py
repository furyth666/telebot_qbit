from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from html import escape
from urllib.parse import parse_qs, unquote, urlparse

import httpx
from telegram.ext import Application

from app.config import Settings
from app.qbit_client import QbitClient


_URL_PATTERN = re.compile(r"(magnet:\?[^\s,，;；|]+|https?://[^\s,，;；|]+)", re.IGNORECASE)
_DIRECT_DOWNLOAD_HINTS = (
    ".torrent",
    "/api/rss/dlv2",
    "/download",
    "download.php",
)


@dataclass(frozen=True)
class AddContext:
    known_hashes: set[str]
    started_at: int
    name_hint: str | None
    is_magnet: bool = False


@dataclass(frozen=True)
class AddBatchResult:
    total_links: int
    success_count: int
    magnet_count: int
    contexts: list[AddContext]
    failures: list[str]


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


def _extract_torrent_links(text: str) -> list[str]:
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
    return any(hint in lowered for hint in _DIRECT_DOWNLOAD_HINTS)


def _text_is_link_only(text: str, links: list[str]) -> bool:
    remainder = text
    for link in links:
        remainder = remainder.replace(link, " ")
    remainder = re.sub(r"[\s,，;；|]+", "", remainder)
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


async def _add_torrent_url(
    application: Application,
    qbit: QbitClient,
    url: str,
) -> dict[str, bool | AddContext]:
    existing_hashes = {item.hash for item in await qbit.list_torrents(filter_name="all")}
    settings: Settings = application.bot_data["settings"]
    upload_limit = (
        _magnet_upload_limit_bytes(settings) if url.lower().startswith("magnet:?") else None
    )
    await qbit.add_torrent_url_with_options(url, upload_limit=upload_limit)
    return {
        "is_magnet": url.lower().startswith("magnet:?"),
        "context": AddContext(
            known_hashes=existing_hashes,
            started_at=int(time.time()),
            name_hint=_extract_name_hint(url),
            is_magnet=url.lower().startswith("magnet:?"),
        ),
    }


def _format_add_failure(index: int, error: Exception) -> str:
    if isinstance(error, httpx.HTTPStatusError):
        reason = f"qBittorrent 返回 {error.response.status_code}"
    elif isinstance(error, RuntimeError):
        reason = str(error)
    else:
        reason = error.__class__.__name__
    return f"第 {index} 条: {escape(reason)}"


async def _add_torrent_links(
    application: Application,
    qbit: QbitClient,
    links: list[str],
) -> AddBatchResult:
    magnet_count = 0
    contexts: list[AddContext] = []
    failures: list[str] = []

    for index, link in enumerate(links, start=1):
        try:
            result = await _add_torrent_url(application, qbit, link)
        except Exception as exc:
            failure = _format_add_failure(index, exc)
            logging.warning("Failed to add torrent link: %s", failure)
            failures.append(failure)
            continue

        if result["is_magnet"]:
            magnet_count += 1
        contexts.append(result["context"])

    return AddBatchResult(
        total_links=len(links),
        success_count=len(contexts),
        magnet_count=magnet_count,
        contexts=contexts,
        failures=failures,
    )


def _format_add_batch_reply(
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
