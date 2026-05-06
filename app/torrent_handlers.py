from __future__ import annotations

from html import escape
from typing import Awaitable, Callable

from telegram import InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import Application, ContextTypes

from app.formatters import (
    _build_list_keyboard,
    _fmt_bytes,
    _fmt_large_file_threshold,
    _fmt_speed,
    _format_action_result,
    _format_torrent_detail,
    _format_torrent_line,
    _format_torrent_overview,
    _fmt_torrent_caption,
)
from app.handler_utils import (
    _callback_action_error,
    _get_hash_argument,
    _reply_qbit_action_error,
    _require_allowed_user,
    _resolve_hash_or_reply,
)
from app.jav_rules import _is_jav_title
from app.jobs import JavFileSelectionResult, _apply_jav_file_selection
from app.qbit_client import QbitClient
from app.runtime_state import _get_jav_pattern, _get_state, _persist_state


async def _render_torrent_detail(
    application: Application,
    torrent_hash: str,
    *,
    view: str = "all",
) -> tuple[str, InlineKeyboardMarkup]:
    qbit: QbitClient = application.bot_data["qbit"]
    item = await qbit.get_torrent(torrent_hash)
    if not item:
        raise ValueError("没有找到对应任务。")

    files = await qbit.get_torrent_files(torrent_hash)
    try:
        props = await qbit.get_torrent_properties(torrent_hash)
    except Exception:
        props = None

    return _format_torrent_detail(item, files, props, view=view)


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
    await update.message.reply_text(
        _format_torrent_overview(title, torrents),
        parse_mode=ParseMode.HTML,
    )
    for index, item in enumerate(visible_torrents, start=1):
        await update.message.reply_text(
            "\n".join(
                [
                    _fmt_torrent_caption(item, index),
                    _format_torrent_line(item),
                ]
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=_build_list_keyboard([item], filter_name=filter_name),
        )


async def list_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _send_torrent_list(update, context, filter_name="all", title="最近任务")


async def active_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _send_torrent_list(update, context, filter_name="active", title="活动任务")


async def detail_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_allowed_user(update, context):
        return
    torrent_hash = _get_hash_argument(context)
    if not torrent_hash:
        await update.message.reply_text("用法: /detail <hash>")
        return
    qbit: QbitClient = context.application.bot_data["qbit"]
    try:
        full_hash = await qbit.resolve_hash(torrent_hash)
        text, keyboard = await _render_torrent_detail(context.application, full_hash)
    except Exception as exc:
        await _reply_qbit_action_error(update, exc)
        return
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)


async def retry_jav_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_allowed_user(update, context):
        return
    torrent_hash = _get_hash_argument(context)
    if not torrent_hash:
        await update.message.reply_text("用法: /retryjav <hash>")
        return

    qbit: QbitClient = context.application.bot_data["qbit"]
    settings = context.application.bot_data["settings"]
    pattern = _get_jav_pattern(context.application)
    try:
        torrent = await qbit.resolve_torrent(torrent_hash)
    except ValueError as exc:
        await update.message.reply_text(str(exc))
        return
    full_hash = torrent.hash
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
    selection_result = await _apply_jav_file_selection(context.application, qbit, full_hash)
    state = _get_state(context.application)
    state.jav_processed_hashes.add(full_hash)
    await _persist_state(context.application)

    notes = [f"<b>已重新处理到 {escape(settings.jav_category_name)}</b>"]
    if selection_result is JavFileSelectionResult.FILTERED:
        notes.append(f"📁 已仅保留大于 {_fmt_large_file_threshold(settings)} 的文件下载，小文件已跳过。")
    elif selection_result is JavFileSelectionResult.NOT_READY:
        notes.append("⚠️ 文件元数据暂未就绪，尚未完成大小筛选；请稍后再次发送 `/retryjav <hash>`。")
    await update.message.reply_text("\n".join(notes), parse_mode=ParseMode.HTML)


async def _torrent_action_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    usage: str,
    action: Callable[[QbitClient, str], Awaitable[None]],
    success_label: str,
) -> None:
    if not await _require_allowed_user(update, context):
        return
    torrent_hash = _get_hash_argument(context)
    if not torrent_hash:
        await update.message.reply_text(usage)
        return
    qbit: QbitClient = context.application.bot_data["qbit"]
    full_hash = await _resolve_hash_or_reply(update, context)
    if not full_hash:
        return
    try:
        await action(qbit, full_hash)
    except Exception as exc:
        await _reply_qbit_action_error(update, exc)
        return
    await update.message.reply_text(
        _format_action_result(success_label, full_hash),
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
    if not await _require_allowed_user(update, context):
        if query:
            await query.answer("无权限使用这个 bot。", show_alert=True)
        return
    if not query or not query.data:
        if query:
            await query.answer()
        return
    try:
        _, action, view, payload = query.data.split(":", 3)
    except ValueError:
        await query.answer("这个按钮已经过期或不可用。", show_alert=True)
        return

    qbit: QbitClient = context.application.bot_data["qbit"]

    try:
        if action == "detail":
            text, keyboard = await _render_torrent_detail(context.application, payload, view=view)
            await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
            await query.answer()
            return

        if action == "pause":
            await qbit.pause_torrent(payload)
            text, keyboard = await _render_torrent_detail(context.application, payload, view=view)
            await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
            await query.answer("已暂停")
            return

        if action == "resume":
            await qbit.resume_torrent(payload)
            text, keyboard = await _render_torrent_detail(context.application, payload, view=view)
            await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
            await query.answer("已恢复")
            return

        if action == "delete":
            await qbit.delete_torrent(payload, delete_files=False)
            await query.edit_message_text(
                _format_action_result("已删除任务，保留文件", payload),
                parse_mode=ParseMode.HTML,
            )
            await query.answer("已删除任务")
            return

        if action == "deletefiles":
            await qbit.delete_torrent(payload, delete_files=True)
            await query.edit_message_text(
                _format_action_result("已删除任务和文件", payload),
                parse_mode=ParseMode.HTML,
            )
            await query.answer("已删除任务和文件")
            return
        await query.answer("这个按钮已经过期或不可用。", show_alert=True)
    except Exception as exc:
        await _callback_action_error(query, exc)
