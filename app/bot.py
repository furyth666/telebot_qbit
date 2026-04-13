from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from html import escape
from urllib.parse import parse_qs, unquote, urlparse

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from app.config import Settings
from app.qbit_client import QbitClient, TorrentFile, TorrentProperties, TorrentSummary
from app.state_store import BotState, StateStore


_STATE_LABELS = {
    "downloading": "⬇️ 下载中",
    "forcedDL": "🚀 强制下载",
    "forcedMetaDL": "🧲 强制获取元数据",
    "metaDL": "🧲 获取元数据",
    "uploading": "⬆️ 做种中",
    "forcedUP": "🚀 强制做种",
    "stalledDL": "⏸️ 等待下载",
    "stalledUP": "⏸️ 等待上传",
    "queuedDL": "🕒 排队下载",
    "queuedUP": "🕒 排队做种",
    "pausedDL": "⏸️ 已暂停",
    "pausedUP": "⏸️ 已暂停",
    "checkingDL": "🔎 校验中",
    "checkingUP": "🔎 校验中",
    "checkingResumeData": "🔄 恢复数据校验",
    "moving": "📦 移动文件中",
    "missingFiles": "📁 文件缺失",
    "error": "❌ 错误",
}

_STATE_ICONS = {
    "downloading": "⬇️",
    "forcedDL": "🚀",
    "forcedMetaDL": "🧲",
    "metaDL": "🧲",
    "uploading": "⬆️",
    "forcedUP": "🚀",
    "stalledDL": "🟡",
    "stalledUP": "🟡",
    "queuedDL": "🕒",
    "queuedUP": "🕒",
    "pausedDL": "⏸️",
    "pausedUP": "⏸️",
    "checkingDL": "🔎",
    "checkingUP": "🔎",
    "checkingResumeData": "🔄",
    "moving": "📦",
    "missingFiles": "📁",
    "error": "❌",
}

_URL_PATTERN = re.compile(r"(magnet:\?[^\s]+|https?://[^\s]+)", re.IGNORECASE)
_DIRECT_DOWNLOAD_HINTS = (
    ".torrent",
    "/api/rss/dlv2",
    "/download",
    "download.php",
)
_CONTEXT_LOOKBACK_SECONDS = 10
_CONTEXT_POLL_ATTEMPTS = 20
_CONTEXT_POLL_INTERVAL_SECONDS = 1
_FILES_POLL_ATTEMPTS = 10
_LIST_FILTERS = {"all", "active"}


@dataclass(frozen=True)
class AddContext:
    known_hashes: set[str]
    started_at: int
    name_hint: str | None


def _magnet_upload_limit_bytes(settings: Settings) -> int:
    return settings.magnet_upload_limit_kib * 1024


def _jav_large_file_threshold_bytes(settings: Settings) -> int:
    return int(settings.jav_large_file_threshold_gb * 1024 * 1024 * 1024)


def _fmt_large_file_threshold(settings: Settings) -> str:
    value = settings.jav_large_file_threshold_gb
    if float(value).is_integer():
        return f"{int(value)} GB"
    return f"{value:g} GB"


def _fmt_bytes(value: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(value)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def _fmt_speed(value: int) -> str:
    return f"{_fmt_bytes(value)}/s"


def _fmt_eta(value: int) -> str:
    if value < 0 or value >= 8640000:
        return "∞"
    hours, remainder = divmod(value, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


def _short_hash(hash_value: str) -> str:
    return hash_value[:8]


def _fmt_state(value: str) -> str:
    return _STATE_LABELS.get(value, value)


def _state_icon(state: str) -> str:
    return _STATE_ICONS.get(state, "🎯")


def _fmt_progress_bar(progress: float, width: int = 10) -> str:
    filled = max(0, min(width, round(progress * width)))
    return f"{'█' * filled}{'░' * (width - filled)}"


def _fmt_progress_text(progress: float) -> str:
    return f"{progress * 100:.1f}%"


def _fmt_torrent_caption(item: TorrentSummary, index: int) -> str:
    return f"{index}. {_state_icon(item.state)} <b>{escape(item.name)}</b>"


def _format_torrent_line(item: TorrentSummary) -> str:
    return (
        f"🏷️ 状态: {_fmt_state(escape(item.state))} | 🔑 Hash: <code>{_short_hash(item.hash)}</code>\n"
        f"📊 进度: <code>{_fmt_progress_bar(item.progress)}</code> {_fmt_progress_text(item.progress)}\n"
        f"💾 大小: {_fmt_bytes(item.size)} | ⏳ ETA: {_fmt_eta(item.eta)}\n"
        f"🚦 速度: ⬇️ {_fmt_speed(item.dlspeed)} | ⬆️ {_fmt_speed(item.upspeed)}"
    )


def _format_torrent_overview(title: str, torrents: list[TorrentSummary]) -> str:
    total_size = sum(item.size for item in torrents)
    active_count = sum(1 for item in torrents if item.dlspeed > 0 or item.upspeed > 0)
    completed_count = sum(1 for item in torrents if item.progress >= 1)
    lines = [
        f"<b>📋 {escape(title)}</b>",
        (
            f"📦 共 {len(torrents)} 个任务 | ⚡ 活跃 {active_count} 个 | "
            f"✅ 完成 {completed_count} 个 | 💾 总大小 {_fmt_bytes(total_size)}"
        ),
    ]
    return "\n".join(lines)


def _format_action_result(action: str, torrent_hash: str) -> str:
    return "\n".join(
        [
            f"<b>{escape(action)}</b>",
            f"🔑 任务 Hash: <code>{escape(torrent_hash)}</code>",
        ]
    )


def _callback_hash(torrent_hash: str) -> str:
    return torrent_hash[:12]


def _fmt_ratio(value: float) -> str:
    if value < 0:
        return "∞"
    return f"{value:.2f}"


def _fmt_timestamp(value: int) -> str:
    if value <= 0:
        return "-"
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(value))


def _truncate_button_label(name: str, limit: int = 28) -> str:
    if len(name) <= limit:
        return name
    return f"{name[: limit - 1]}…"


def _extract_links(text: str) -> list[str]:
    seen: set[str] = set()
    links: list[str] = []
    for match in _URL_PATTERN.findall(text):
        candidate = match.strip().strip("<>\"'(),")
        if candidate and candidate not in seen:
            seen.add(candidate)
            links.append(candidate)
    return links


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


def _get_jav_pattern(application: Application) -> re.Pattern[str]:
    return application.bot_data["jav_name_pattern"]


def _get_state_store(application: Application) -> StateStore:
    return application.bot_data["state_store"]


def _get_state(application: Application) -> BotState:
    return application.bot_data["bot_state"]


def _persist_state(application: Application) -> None:
    _get_state_store(application).save(_get_state(application))


def _build_list_markup(torrents: list[TorrentSummary], filter_name: str) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                f"{_state_icon(item.state)} {_truncate_button_label(item.name)}",
                callback_data=f"d:{filter_name}:{_callback_hash(item.hash)}",
            )
        ]
        for item in torrents[:10]
    ]
    if filter_name == "all":
        rows.append([InlineKeyboardButton("⚡ 只看活动任务", callback_data="l:active")])
    else:
        rows.append([InlineKeyboardButton("📋 查看全部任务", callback_data="l:all")])
    return InlineKeyboardMarkup(rows)


def _build_detail_markup(item: TorrentSummary, filter_name: str) -> InlineKeyboardMarkup:
    short_hash = _callback_hash(item.hash)
    paused = item.state in {"pausedDL", "pausedUP"}
    action_label = "▶️ 恢复" if paused else "⏸️ 暂停"
    action_code = "resume" if paused else "pause"
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(action_label, callback_data=f"a:{action_code}:{filter_name}:{short_hash}"),
                InlineKeyboardButton("🔄 刷新", callback_data=f"d:{filter_name}:{short_hash}"),
            ],
            [
                InlineKeyboardButton("🗑️ 删除任务", callback_data=f"a:delete:{filter_name}:{short_hash}"),
                InlineKeyboardButton("🔥 删除含文件", callback_data=f"a:deletefiles:{filter_name}:{short_hash}"),
            ],
            [InlineKeyboardButton("⬅️ 返回列表", callback_data=f"l:{filter_name}")],
        ]
    )


def _format_file_selection_summary(files: list[TorrentFile]) -> str:
    if not files:
        return "未知"
    selected = [item for item in files if item.priority > 0]
    skipped = [item for item in files if item.priority == 0]
    return f"已选 {len(selected)} / 跳过 {len(skipped)}"


def _format_torrent_detail(
    item: TorrentSummary,
    properties: TorrentProperties,
    files: list[TorrentFile],
) -> str:
    category = item.category or "-"
    lines = [
        f"<b>{escape(item.name)}</b>",
        f"🏷️ 状态: {_fmt_state(item.state)}",
        f"🗂️ 分类: <code>{escape(category)}</code>",
        f"🔑 Hash: <code>{escape(item.hash)}</code>",
        f"📊 进度: <code>{_fmt_progress_bar(item.progress)}</code> {_fmt_progress_text(item.progress)}",
        f"💾 大小: {_fmt_bytes(properties.total_size or item.size)}",
        f"🚦 速度: ⬇️ {_fmt_speed(item.dlspeed)} | ⬆️ {_fmt_speed(item.upspeed)}",
        f"⏳ ETA: {_fmt_eta(item.eta)} | ♻️ 分享率: {_fmt_ratio(properties.share_ratio)}",
        f"📤 已上传: {_fmt_bytes(properties.total_uploaded)}",
        f"📁 文件: {_format_file_selection_summary(files)}",
        f"📂 路径: <code>{escape(properties.save_path or '-')}</code>",
        f"🕓 完成时间: {_fmt_timestamp(properties.completion_date)}",
    ]
    return "\n".join(lines)


async def _apply_jav_category_to_new_torrents(
    application: Application,
    qbit: QbitClient,
    context: AddContext,
) -> list[TorrentSummary]:
    settings: Settings = application.bot_data["settings"]
    pattern = _get_jav_pattern(application)
    processed_hashes = _get_state(application).jav_processed_hashes

    try:
        await qbit.create_category(settings.jav_category_name)
    except Exception:
        pass
    categorized: dict[str, TorrentSummary] = {}

    for _ in range(_CONTEXT_POLL_ATTEMPTS):
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
        await asyncio.sleep(_CONTEXT_POLL_INTERVAL_SECONDS)

    return []


async def _apply_jav_file_selection(
    application: Application,
    qbit: QbitClient,
    torrent_hash: str,
) -> bool:
    threshold = _jav_large_file_threshold_bytes(application.bot_data["settings"])
    for _ in range(_FILES_POLL_ATTEMPTS):
        files = await qbit.get_torrent_files(torrent_hash)
        if not files:
            await asyncio.sleep(1)
            continue

        large_files = [item for item in files if item.size > threshold]
        small_files = [item for item in files if item.size <= threshold]
        if not large_files or not small_files:
            return False

        await qbit.set_file_priority(torrent_hash, [item.index for item in large_files], 1)
        await qbit.set_file_priority(torrent_hash, [item.index for item in small_files], 0)
        return True

    return False


async def _notify_completion_loop(application: Application) -> None:
    settings: Settings = application.bot_data["settings"]
    qbit: QbitClient = application.bot_data["qbit"]
    state = _get_state(application)

    while True:
        try:
            torrents = await qbit.list_torrents(filter_name="all")
            for item in torrents:
                if item.hash in state.notified_completed_hashes:
                    continue
                if item.progress < 1 and item.completion_on <= 0:
                    continue

                state.notified_completed_hashes.add(item.hash)
                _persist_state(application)
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
        if categorized:
            state = _get_state(application)
            filtered_count = 0
            for item in categorized:
                state.jav_processed_hashes.add(item.hash)
                if await _apply_jav_file_selection(application, qbit, item.hash):
                    filtered_count += 1
            _persist_state(application)

            if len(categorized) == 1:
                notes = [
                    f"<b>🗂️ 已自动分类到 {escape(settings.jav_category_name)}</b>",
                    "检测到新任务名称包含“多个字母-多个数字”的格式。",
                ]
                if filtered_count:
                    notes.append(f"📁 已仅保留大于 {threshold_text} 的文件下载，小文件已跳过。")
            else:
                notes = [
                    f"<b>🗂️ 已自动分类 {len(categorized)} 个任务到 {escape(settings.jav_category_name)}</b>",
                    "检测到这些新任务名称包含“多个字母-多个数字”的格式。",
                ]
                if filtered_count:
                    notes.append(
                        f"📁 其中 {filtered_count} 个任务已仅保留大于 {threshold_text} 的文件下载，小文件已跳过。"
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
        ),
    }


async def _require_allowed_user(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> bool:
    settings: Settings = context.application.bot_data["settings"]
    user = update.effective_user
    if not user or user.id not in settings.telegram_allowed_user_ids:
        if update.effective_message:
            await update.effective_message.reply_text("无权限使用这个 bot。")
        return False
    return True


async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_allowed_user(update, context):
        return
    await update.message.reply_text(
        (
            "<b>qBittorrent 管理 bot 已启动</b>\n\n"
            "可用命令:\n"
            "📈 /status - 查看整体状态\n"
            "📋 /list - 查看最近 10 个任务，可点按钮看详情\n"
            "⚡ /active - 查看活动任务，可点按钮操作\n"
            "⏸️ /pause &lt;hash&gt; - 暂停任务\n"
            "▶️ /resume &lt;hash&gt; - 恢复任务\n"
            "🗑️ /delete &lt;hash&gt; - 删除任务但保留文件\n"
            "🔥 /deletefiles &lt;hash&gt; - 删除任务和文件\n"
            "➕ /add &lt;magnet或torrent链接&gt; - 添加下载\n"
            "🔁 /retryjav &lt;hash&gt; - 重新执行 JAV 分类\n"
            "📎 也可以直接发送 magnet、.torrent 或下载直链"
        ),
        parse_mode=ParseMode.HTML,
    )


async def help_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await start_handler(update, context)


async def status_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_allowed_user(update, context):
        return

    qbit: QbitClient = context.application.bot_data["qbit"]
    info = await qbit.get_transfer_info()
    await update.message.reply_text(
        (
            "<b>📈 qBittorrent 状态</b>\n"
            f"🚦 实时速度: ⬇️ {_fmt_speed(int(info.get('dl_info_speed', 0)))} | "
            f"⬆️ {_fmt_speed(int(info.get('up_info_speed', 0)))}\n"
            f"📊 累计流量: ⬇️ {_fmt_bytes(int(info.get('dl_info_data', 0)))} | "
            f"⬆️ {_fmt_bytes(int(info.get('up_info_data', 0)))}\n"
            f"🌐 DHT 节点: {info.get('dht_nodes', 0)}\n"
            f"🔌 连接状态: {escape(str(info.get('connection_status', 'unknown')))}"
        ),
        parse_mode=ParseMode.HTML,
    )


async def _send_torrent_list(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    filter_name: str,
    title: str,
) -> None:
    if not await _require_allowed_user(update, context):
        return

    qbit: QbitClient = context.application.bot_data["qbit"]
    torrents = await qbit.list_torrents(filter_name=filter_name)

    if not torrents:
        await update.message.reply_text(
            f"<b>📋 {escape(title)}</b>\n😌 当前没有任务。",
            parse_mode=ParseMode.HTML,
        )
        return

    visible_torrents = torrents[:10]
    preview_lines = [
        _fmt_torrent_caption(item, index) for index, item in enumerate(visible_torrents, start=1)
    ]
    body = "\n".join(preview_lines)
    await update.message.reply_text(
        f"{_format_torrent_overview(title, torrents)}\n\n{body}\n\n👇 点下面按钮查看详情",
        parse_mode=ParseMode.HTML,
        reply_markup=_build_list_markup(visible_torrents, filter_name),
    )


async def list_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _send_torrent_list(update, context, filter_name="all", title="最近任务")


async def active_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _send_torrent_list(update, context, filter_name="active", title="活动任务")


async def _edit_torrent_list_message(
    query,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    filter_name: str,
) -> None:
    qbit: QbitClient = context.application.bot_data["qbit"]
    torrents = await qbit.list_torrents(filter_name=filter_name)
    title = "最近任务" if filter_name == "all" else "活动任务"
    if not torrents:
        await query.edit_message_text(
            f"<b>📋 {escape(title)}</b>\n😌 当前没有任务。",
            parse_mode=ParseMode.HTML,
        )
        return

    visible_torrents = torrents[:10]
    preview_lines = [
        _fmt_torrent_caption(item, index) for index, item in enumerate(visible_torrents, start=1)
    ]
    await query.edit_message_text(
        f"{_format_torrent_overview(title, torrents)}\n\n" + "\n".join(preview_lines) + "\n\n👇 点下面按钮查看详情",
        parse_mode=ParseMode.HTML,
        reply_markup=_build_list_markup(visible_torrents, filter_name),
    )


async def _edit_torrent_detail_message(
    query,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    filter_name: str,
    hash_prefix: str,
) -> None:
    qbit: QbitClient = context.application.bot_data["qbit"]
    torrent_hash = await qbit.resolve_hash(hash_prefix)
    item = await qbit.get_torrent(torrent_hash)
    if not item:
        await query.edit_message_text("任务不存在或已被删除。")
        return
    properties = await qbit.get_torrent_properties(torrent_hash)
    files = await qbit.get_torrent_files(torrent_hash)
    await query.edit_message_text(
        _format_torrent_detail(item, properties, files),
        parse_mode=ParseMode.HTML,
        reply_markup=_build_detail_markup(item, filter_name),
    )


def _get_hash_argument(context: ContextTypes.DEFAULT_TYPE) -> str | None:
    if not context.args:
        return None
    return context.args[0].strip()


async def retry_jav_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_allowed_user(update, context):
        return
    torrent_hash = _get_hash_argument(context)
    if not torrent_hash:
        await update.message.reply_text("用法: /retryjav <hash>")
        return

    qbit: QbitClient = context.application.bot_data["qbit"]
    settings: Settings = context.application.bot_data["settings"]
    pattern = _get_jav_pattern(context.application)
    full_hash = await qbit.resolve_hash(torrent_hash)
    torrent = await qbit.get_torrent(full_hash)
    if not torrent:
        await update.message.reply_text("没有找到对应任务。")
        return
    if not _is_jav_title(torrent.name, pattern):
        await update.message.reply_text(
            "<b>未命中当前 JAV 规则</b>\n"
            f"当前规则: <code>{escape(settings.jav_name_regex)}</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    try:
        await qbit.create_category(settings.jav_category_name)
    except Exception:
        pass
    await qbit.set_category(full_hash, settings.jav_category_name)
    filtered = await _apply_jav_file_selection(context.application, qbit, full_hash)
    state = _get_state(context.application)
    state.jav_processed_hashes.add(full_hash)
    _persist_state(context.application)

    notes = [f"<b>已重新处理到 {escape(settings.jav_category_name)}</b>"]
    if filtered:
        notes.append(f"📁 已仅保留大于 {_fmt_large_file_threshold(settings)} 的文件下载，小文件已跳过。")
    await update.message.reply_text("\n".join(notes), parse_mode=ParseMode.HTML)


async def pause_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_allowed_user(update, context):
        return
    torrent_hash = _get_hash_argument(context)
    if not torrent_hash:
        await update.message.reply_text("用法: /pause <hash>")
        return
    qbit: QbitClient = context.application.bot_data["qbit"]
    torrent_hash = await qbit.resolve_hash(torrent_hash)
    await qbit.pause_torrent(torrent_hash)
    await update.message.reply_text(
        _format_action_result("已暂停任务", torrent_hash),
        parse_mode=ParseMode.HTML,
    )


async def resume_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_allowed_user(update, context):
        return
    torrent_hash = _get_hash_argument(context)
    if not torrent_hash:
        await update.message.reply_text("用法: /resume <hash>")
        return
    qbit: QbitClient = context.application.bot_data["qbit"]
    torrent_hash = await qbit.resolve_hash(torrent_hash)
    await qbit.resume_torrent(torrent_hash)
    await update.message.reply_text(
        _format_action_result("已恢复任务", torrent_hash),
        parse_mode=ParseMode.HTML,
    )


async def delete_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_allowed_user(update, context):
        return
    torrent_hash = _get_hash_argument(context)
    if not torrent_hash:
        await update.message.reply_text("用法: /delete <hash>")
        return
    qbit: QbitClient = context.application.bot_data["qbit"]
    torrent_hash = await qbit.resolve_hash(torrent_hash)
    await qbit.delete_torrent(torrent_hash, delete_files=False)
    await update.message.reply_text(
        _format_action_result("已删除任务，保留文件", torrent_hash),
        parse_mode=ParseMode.HTML,
    )


async def delete_files_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    if not await _require_allowed_user(update, context):
        return
    torrent_hash = _get_hash_argument(context)
    if not torrent_hash:
        await update.message.reply_text("用法: /deletefiles <hash>")
        return
    qbit: QbitClient = context.application.bot_data["qbit"]
    torrent_hash = await qbit.resolve_hash(torrent_hash)
    await qbit.delete_torrent(torrent_hash, delete_files=True)
    await update.message.reply_text(
        _format_action_result("已删除任务和文件", torrent_hash),
        parse_mode=ParseMode.HTML,
    )


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    if not await _require_allowed_user(update, context):
        await query.answer()
        return

    data = query.data or ""
    await query.answer()

    if data.startswith("l:"):
        filter_name = data.split(":", 1)[1]
        if filter_name not in _LIST_FILTERS:
            return
        await _edit_torrent_list_message(query, context, filter_name=filter_name)
        return

    if data.startswith("d:"):
        _, filter_name, hash_prefix = data.split(":", 2)
        await _edit_torrent_detail_message(
            query,
            context,
            filter_name=filter_name,
            hash_prefix=hash_prefix,
        )
        return

    if data.startswith("a:"):
        _, action, filter_name, hash_prefix = data.split(":", 3)
        qbit: QbitClient = context.application.bot_data["qbit"]
        torrent_hash = await qbit.resolve_hash(hash_prefix)
        if action == "pause":
            await qbit.pause_torrent(torrent_hash)
        elif action == "resume":
            await qbit.resume_torrent(torrent_hash)
        elif action == "delete":
            await qbit.delete_torrent(torrent_hash, delete_files=False)
            await _edit_torrent_list_message(query, context, filter_name=filter_name)
            return
        elif action == "deletefiles":
            await qbit.delete_torrent(torrent_hash, delete_files=True)
            await _edit_torrent_list_message(query, context, filter_name=filter_name)
            return
        await _edit_torrent_detail_message(
            query,
            context,
            filter_name=filter_name,
            hash_prefix=torrent_hash,
        )
        return


async def add_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_allowed_user(update, context):
        return
    if not context.args:
        await update.message.reply_text("用法: /add <magnet或torrent链接>")
        return
    torrent_url = " ".join(context.args).strip()
    qbit: QbitClient = context.application.bot_data["qbit"]
    chat = update.effective_chat
    if not chat:
        return
    result = await _add_torrent_url(context.application, qbit, torrent_url)
    notes = ["<b>➕ 已提交添加请求</b>"]
    if result["is_magnet"]:
        limit_kib = context.application.bot_data["settings"].magnet_upload_limit_kib
        notes.append(f"📤 该 magnet 任务上传限速已设为 {limit_kib} KB/s")
    await update.message.reply_text("\n".join(notes), parse_mode=ParseMode.HTML)
    context.application.create_task(
        _background_finalize_torrent(
            context.application,
            qbit,
            result["context"],
            chat.id,
        )
    )


async def text_link_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_allowed_user(update, context):
        return
    message = update.effective_message
    text = message.text if message else None
    if not text:
        return

    links = _extract_links(text)
    if not links:
        return

    candidate_links = [link for link in links if _looks_like_torrent_link(link)]
    if not candidate_links and _text_is_link_only(text, links):
        candidate_links = links

    if not candidate_links:
        return

    qbit: QbitClient = context.application.bot_data["qbit"]
    chat = update.effective_chat
    if not chat:
        return
    magnet_count = 0
    background_contexts: list[AddContext] = []
    for link in candidate_links:
        result = await _add_torrent_url(context.application, qbit, link)
        if result["is_magnet"]:
            magnet_count += 1
        background_contexts.append(result["context"])

    if len(candidate_links) == 1:
        notes = ["<b>➕ 已自动识别并添加下载链接</b>"]
        if magnet_count == 1:
            limit_kib = context.application.bot_data["settings"].magnet_upload_limit_kib
            notes.append(f"📤 该 magnet 任务上传限速已设为 {limit_kib} KB/s")
        await message.reply_text("\n".join(notes), parse_mode=ParseMode.HTML)
        for add_context in background_contexts:
            context.application.create_task(
                _background_finalize_torrent(
                    context.application,
                    qbit,
                    add_context,
                    chat.id,
                )
            )
        return

    extra_notes: list[str] = []
    if magnet_count:
        limit_kib = context.application.bot_data["settings"].magnet_upload_limit_kib
        extra_notes.append(
            f"📤 其中 {magnet_count} 个 magnet 任务上传限速已设为 {limit_kib} KB/s"
        )
    await message.reply_text(
        "\n".join([f"<b>➕ 已自动识别并添加 {len(candidate_links)} 个下载链接</b>", *extra_notes]),
        parse_mode=ParseMode.HTML,
    )
    for add_context in background_contexts:
        context.application.create_task(
            _background_finalize_torrent(
                context.application,
                qbit,
                add_context,
                chat.id,
            )
        )


async def error_handler(_: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logging.exception("Unhandled bot error", exc_info=context.error)


async def post_init(application: Application) -> None:
    settings: Settings = application.bot_data["settings"]
    await application.bot.set_my_commands(
        [
            BotCommand("start", "显示欢迎信息和命令说明"),
            BotCommand("help", "查看命令帮助"),
            BotCommand("status", "查看 qBittorrent 整体状态"),
            BotCommand("list", "查看最近 10 个任务和按钮详情"),
            BotCommand("active", "查看活动任务和按钮操作"),
            BotCommand("pause", "暂停任务，用法: /pause <hash>"),
            BotCommand("resume", "恢复任务，用法: /resume <hash>"),
            BotCommand("delete", "删除任务并保留文件"),
            BotCommand("deletefiles", "删除任务和文件"),
            BotCommand("add", "添加磁力链接或 torrent 链接"),
            BotCommand("retryjav", "重新执行 JAV 分类和文件筛选"),
        ]
    )
    application.bot_data["jav_name_pattern"] = re.compile(settings.jav_name_regex)
    state_store = StateStore(settings.state_file_path)
    state = state_store.load()
    application.bot_data["state_store"] = state_store
    application.bot_data["bot_state"] = state

    qbit: QbitClient = application.bot_data["qbit"]
    existing = await qbit.list_torrents(filter_name="all")
    state.notified_completed_hashes.update(
        item.hash for item in existing if item.progress >= 1 or item.completion_on > 0
    )
    _persist_state(application)
    application.bot_data["completion_monitor_task"] = asyncio.create_task(
        _notify_completion_loop(application)
    )


async def post_shutdown(application: Application) -> None:
    task = application.bot_data.get("completion_monitor_task")
    if task:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    _persist_state(application)

    qbit: QbitClient = application.bot_data["qbit"]
    await qbit.close()


def create_application(settings: Settings) -> Application:
    application = (
        Application.builder()
        .token(settings.telegram_bot_token)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )
    application.bot_data["settings"] = settings
    application.bot_data["qbit"] = QbitClient(
        settings.qbit_base_url,
        settings.qbit_username,
        settings.qbit_password,
    )

    application.add_handler(CommandHandler("start", start_handler))
    application.add_handler(CommandHandler("help", help_handler))
    application.add_handler(CommandHandler("status", status_handler))
    application.add_handler(CommandHandler("list", list_handler))
    application.add_handler(CommandHandler("active", active_handler))
    application.add_handler(CommandHandler("pause", pause_handler))
    application.add_handler(CommandHandler("resume", resume_handler))
    application.add_handler(CommandHandler("delete", delete_handler))
    application.add_handler(CommandHandler("deletefiles", delete_files_handler))
    application.add_handler(CommandHandler("add", add_handler))
    application.add_handler(CommandHandler("retryjav", retry_jav_handler))
    application.add_handler(CallbackQueryHandler(callback_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_link_handler))
    application.add_error_handler(error_handler)
    return application
