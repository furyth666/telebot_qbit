from __future__ import annotations

from html import escape
from typing import Awaitable, Callable

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from app.callback_actions import handle_torrent_callback_action, render_torrent_detail
from app.callback_data import parse_torrent_callback
from app.formatters import (
    build_list_keyboard,
    format_action_result,
    format_bytes,
    format_large_file_threshold,
    format_speed,
    format_torrent_caption,
    format_torrent_line,
    format_torrent_overview,
)
from app.handler_utils import (
    callback_action_error,
    get_hash_argument,
    reply_qbit_action_error,
    require_allowed_user,
    resolve_hash_or_reply,
)
from app.jav_policy import JavFileSelectionResult, apply_jav_category_policy
from app.jav_rules import is_jav_title
from app.qbit_client import QbitClient
from app.runtime_state import get_jav_pattern, runtime_context


async def status_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_allowed_user(update, context):
        return

    qbit: QbitClient = runtime_context(context.application).qbit
    info = await qbit.get_transfer_info()
    await update.message.reply_text(
        (
            "<b>📈 qBittorrent 状态</b>\n"
            f"🚦 实时速度: ⬇️ {format_speed(int(info.get('dl_info_speed', 0)))} | "
            f"⬆️ {format_speed(int(info.get('up_info_speed', 0)))}\n"
            f"📊 累计流量: ⬇️ {format_bytes(int(info.get('dl_info_data', 0)))} | "
            f"⬆️ {format_bytes(int(info.get('up_info_data', 0)))}\n"
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
    if not await require_allowed_user(update, context):
        return

    qbit: QbitClient = runtime_context(context.application).qbit
    torrents = await qbit.list_torrents(filter_name=filter_name)

    if not torrents:
        await update.message.reply_text(
            f"<b>📋 {escape(title)}</b>\n😌 当前没有任务。",
            parse_mode=ParseMode.HTML,
        )
        return

    visible_torrents = torrents[:10]
    await update.message.reply_text(
        format_torrent_overview(title, torrents),
        parse_mode=ParseMode.HTML,
    )
    for index, item in enumerate(visible_torrents, start=1):
        await update.message.reply_text(
            "\n".join(
                [
                    format_torrent_caption(item, index),
                    format_torrent_line(item),
                ]
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=build_list_keyboard([item], filter_name=filter_name),
        )


async def list_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _send_torrent_list(update, context, filter_name="all", title="最近任务")


async def active_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _send_torrent_list(update, context, filter_name="active", title="活动任务")


async def detail_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_allowed_user(update, context):
        return
    torrent_hash = get_hash_argument(context)
    if not torrent_hash:
        await update.message.reply_text("用法: /detail <hash>")
        return
    qbit: QbitClient = runtime_context(context.application).qbit
    try:
        full_hash = await qbit.resolve_hash(torrent_hash)
        text, keyboard = await render_torrent_detail(context.application, full_hash)
    except Exception as exc:
        await reply_qbit_action_error(update, exc)
        return
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)


async def retry_jav_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_allowed_user(update, context):
        return
    torrent_hash = get_hash_argument(context)
    if not torrent_hash:
        await update.message.reply_text("用法: /retryjav <hash>")
        return

    runtime = runtime_context(context.application)
    qbit: QbitClient = runtime.qbit
    settings = runtime.settings
    pattern = get_jav_pattern(context.application)
    try:
        torrent = await qbit.resolve_torrent(torrent_hash)
    except ValueError as exc:
        await update.message.reply_text(str(exc))
        return
    full_hash = torrent.hash
    if not is_jav_title(torrent.name, pattern):
        await update.message.reply_text(
            "<b>未命中当前 JAV 规则</b>\n"
            f"当前规则: <code>{escape(settings.jav_name_regex)}</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    result = await apply_jav_category_policy(context.application, qbit, full_hash)

    notes = [f"<b>已重新处理到 {escape(result.category)}</b>"]
    if result.selection_result is JavFileSelectionResult.FILTERED:
        notes.append(f"📁 已仅保留大于 {format_large_file_threshold(settings)} 的文件下载，小文件已跳过。")
    elif result.selection_result is JavFileSelectionResult.NOT_READY:
        notes.append("⚠️ 文件元数据暂未就绪，尚未完成大小筛选；请稍后再次发送 <code>/retryjav &lt;hash&gt;</code>。")
    await update.message.reply_text("\n".join(notes), parse_mode=ParseMode.HTML)


async def _torrent_action_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    usage: str,
    action: Callable[[QbitClient, str], Awaitable[None]],
    success_label: str,
) -> None:
    if not await require_allowed_user(update, context):
        return
    torrent_hash = get_hash_argument(context)
    if not torrent_hash:
        await update.message.reply_text(usage)
        return
    qbit: QbitClient = runtime_context(context.application).qbit
    full_hash = await resolve_hash_or_reply(update, context)
    if not full_hash:
        return
    try:
        await action(qbit, full_hash)
    except Exception as exc:
        await reply_qbit_action_error(update, exc)
        return
    await update.message.reply_text(
        format_action_result(success_label, full_hash),
        parse_mode=ParseMode.HTML,
    )


async def pause_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _torrent_action_handler(
        update,
        context,
        usage="用法: /pause <hash>",
        action=lambda qbit, torrent_hash: qbit.pause_torrent(torrent_hash),
        success_label="已暂停任务",
    )


async def resume_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _torrent_action_handler(
        update,
        context,
        usage="用法: /resume <hash>",
        action=lambda qbit, torrent_hash: qbit.resume_torrent(torrent_hash),
        success_label="已恢复任务",
    )


async def delete_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _torrent_action_handler(
        update,
        context,
        usage="用法: /delete <hash>",
        action=lambda qbit, torrent_hash: qbit.delete_torrent(
            torrent_hash,
            delete_files=False,
        ),
        success_label="已删除任务，保留文件",
    )


async def delete_files_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    await _torrent_action_handler(
        update,
        context,
        usage="用法: /deletefiles <hash>",
        action=lambda qbit, torrent_hash: qbit.delete_torrent(
            torrent_hash,
            delete_files=True,
        ),
        success_label="已删除任务和文件",
    )


async def torrent_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not await require_allowed_user(update, context):
        if query:
            await query.answer("无权限使用这个 bot。", show_alert=True)
        return
    if not query or not query.data:
        if query:
            await query.answer()
        return
    callback = parse_torrent_callback(query.data)
    if callback is None:
        await query.answer("这个按钮已经过期或不可用。", show_alert=True)
        return

    try:
        handled = await handle_torrent_callback_action(context.application, query, callback)
        if not handled:
            await query.answer("这个按钮已经过期或不可用。", show_alert=True)
    except Exception as exc:
        await callback_action_error(query, exc)
