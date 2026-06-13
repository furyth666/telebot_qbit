from __future__ import annotations

import logging
import time

from telegram import Update
from telegram.constants import ParseMode
from telegram.error import NetworkError
from telegram.ext import ContextTypes

from app.handler_utils import require_allowed_user
from app.runtime_state import runtime_context


async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_allowed_user(update, context):
        return
    await update.message.reply_text(
        (
            "<b>qBittorrent 管理 bot 已启动</b>\n\n"
            "可用命令:\n"
            "📈 /status - 查看整体状态\n"
            "📋 /list - 查看最近 10 个任务\n"
            "⚡ /active - 查看活动任务\n"
            "🎯 /detail &lt;hash&gt; - 查看任务详情\n"
            "⏸️ /pause &lt;hash&gt; - 暂停任务\n"
            "▶️ /resume &lt;hash&gt; - 恢复任务\n"
            "🗑️ /delete &lt;hash&gt; - 删除任务但保留文件\n"
            "🔥 /deletefiles &lt;hash&gt; - 删除任务和文件\n"
            "➕ /add &lt;一个或多个链接&gt; - 添加下载\n"
            "🎬 /jav &lt;番号&gt; - 查询 Jellyfin 里的同番号影片\n"
            "🔁 /retryjav &lt;hash&gt; - 重新执行 JAV 分类\n"
            "📎 也可以直接发送 magnet、.torrent、下载直链，或直接发送番号查询 Jellyfin"
        ),
        parse_mode=ParseMode.HTML,
    )


async def help_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await start_handler(update, context)


async def error_handler(_: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logging.exception("Unhandled bot error", exc_info=context.error)
    if not isinstance(context.error, NetworkError):
        return

    runtime = runtime_context(context.application)
    settings = runtime.settings
    now = time.monotonic()
    window_start = now - settings.telegram_network_error_window_seconds
    failures = [
        item
        for item in runtime.telegram_network_error_times
        if item >= window_start
    ]
    failures.append(now)
    runtime.telegram_network_error_times = failures
    if len(failures) >= settings.telegram_network_error_restart_threshold:
        logging.critical(
            "Telegram network error threshold reached (%s/%s seconds); requesting restart",
            len(failures),
            settings.telegram_network_error_window_seconds,
        )
        context.application.stop_running()
