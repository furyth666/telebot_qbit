from __future__ import annotations

import logging
import re
from html import escape

from telegram import BotCommand, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from app.config import Settings
from app.qbit_client import QbitClient, TorrentSummary


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
_MAGNET_UPLOAD_LIMIT_BYTES = 30 * 1024


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


async def _add_torrent_url(qbit: QbitClient, url: str) -> None:
    upload_limit = _MAGNET_UPLOAD_LIMIT_BYTES if url.lower().startswith("magnet:?") else None
    await qbit.add_torrent_url_with_options(url, upload_limit=upload_limit)


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
            "📋 /list - 查看最近 10 个任务\n"
            "⚡ /active - 查看活动任务\n"
            "⏸️ /pause &lt;hash&gt; - 暂停任务\n"
            "▶️ /resume &lt;hash&gt; - 恢复任务\n"
            "🗑️ /delete &lt;hash&gt; - 删除任务但保留文件\n"
            "🔥 /deletefiles &lt;hash&gt; - 删除任务和文件\n"
            "➕ /add &lt;magnet或torrent链接&gt; - 添加下载\n"
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
    entries: list[str] = []
    for index, item in enumerate(visible_torrents, start=1):
        entries.append(
            "\n".join(
                [
                    _fmt_torrent_caption(item, index),
                    _format_torrent_line(item),
                ]
            )
        )

    body = "\n\n".join(entries)
    await update.message.reply_text(
        f"{_format_torrent_overview(title, torrents)}\n\n{body}",
        parse_mode=ParseMode.HTML,
    )


async def list_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _send_torrent_list(update, context, filter_name="all", title="最近任务")


async def active_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _send_torrent_list(update, context, filter_name="active", title="活动任务")


def _get_hash_argument(context: ContextTypes.DEFAULT_TYPE) -> str | None:
    if not context.args:
        return None
    return context.args[0].strip()


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


async def add_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_allowed_user(update, context):
        return
    if not context.args:
        await update.message.reply_text("用法: /add <magnet或torrent链接>")
        return
    torrent_url = " ".join(context.args).strip()
    qbit: QbitClient = context.application.bot_data["qbit"]
    await _add_torrent_url(qbit, torrent_url)
    if torrent_url.lower().startswith("magnet:?"):
        await update.message.reply_text(
            "<b>➕ 已提交添加请求</b>\n📤 该 magnet 任务上传限速已设为 30 KB/s",
            parse_mode=ParseMode.HTML,
        )
        return
    await update.message.reply_text("<b>➕ 已提交添加请求</b>", parse_mode=ParseMode.HTML)


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
    magnet_count = 0
    for link in candidate_links:
        if link.lower().startswith("magnet:?"):
            magnet_count += 1
        await _add_torrent_url(qbit, link)

    if len(candidate_links) == 1:
        if magnet_count == 1:
            await message.reply_text(
                "<b>➕ 已自动识别并添加下载链接</b>\n📤 该 magnet 任务上传限速已设为 30 KB/s",
                parse_mode=ParseMode.HTML,
            )
            return
        await message.reply_text("<b>➕ 已自动识别并添加下载链接</b>", parse_mode=ParseMode.HTML)
        return

    extra_note = ""
    if magnet_count:
        extra_note = f"\n📤 其中 {magnet_count} 个 magnet 任务上传限速已设为 30 KB/s"
    await message.reply_text(
        f"<b>➕ 已自动识别并添加 {len(candidate_links)} 个下载链接</b>{extra_note}",
        parse_mode=ParseMode.HTML,
    )


async def error_handler(_: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logging.exception("Unhandled bot error", exc_info=context.error)


async def post_init(application: Application) -> None:
    await application.bot.set_my_commands(
        [
            BotCommand("start", "显示欢迎信息和命令说明"),
            BotCommand("help", "查看命令帮助"),
            BotCommand("status", "查看 qBittorrent 整体状态"),
            BotCommand("list", "查看最近 10 个任务"),
            BotCommand("active", "查看活动任务"),
            BotCommand("pause", "暂停任务，用法: /pause <hash>"),
            BotCommand("resume", "恢复任务，用法: /resume <hash>"),
            BotCommand("delete", "删除任务并保留文件"),
            BotCommand("deletefiles", "删除任务和文件"),
            BotCommand("add", "添加磁力链接或 torrent 链接"),
        ]
    )


def create_application(settings: Settings) -> Application:
    application = (
        Application.builder()
        .token(settings.telegram_bot_token)
        .post_init(post_init)
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
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_link_handler))
    application.add_error_handler(error_handler)
    return application
