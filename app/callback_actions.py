from __future__ import annotations

from html import escape

from telegram import InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import Application

from app.callback_data import TorrentCallback, parse_category_callback_payload
from app.category_flow import apply_manual_category_choice
from app.formatters import format_action_result, format_torrent_detail
from app.qbit_client import QbitClient
from app.runtime_state import runtime_context

__all__ = [
    "handle_torrent_callback_action",
    "render_torrent_detail",
]


async def render_torrent_detail(
    application: Application,
    torrent_hash: str,
    *,
    view: str = "all",
) -> tuple[str, InlineKeyboardMarkup]:
    qbit: QbitClient = runtime_context(application).qbit
    item = await qbit.get_torrent(torrent_hash)
    if not item:
        raise ValueError("没有找到对应任务。")

    files = await qbit.get_torrent_files(torrent_hash)
    try:
        props = await qbit.get_torrent_properties(torrent_hash)
    except Exception:
        props = None

    return format_torrent_detail(item, files, props, view=view)


async def handle_torrent_callback_action(
    application: Application,
    query,
    callback: TorrentCallback,
) -> bool:
    qbit: QbitClient = runtime_context(application).qbit

    if callback.action == "detail":
        text, keyboard = await render_torrent_detail(
            application,
            callback.payload,
            view=callback.view,
        )
        await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
        await query.answer()
        return True

    if callback.action == "pause":
        await qbit.pause_torrent(callback.payload)
        text, keyboard = await render_torrent_detail(
            application,
            callback.payload,
            view=callback.view,
        )
        await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
        await query.answer("已暂停")
        return True

    if callback.action == "resume":
        await qbit.resume_torrent(callback.payload)
        text, keyboard = await render_torrent_detail(
            application,
            callback.payload,
            view=callback.view,
        )
        await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
        await query.answer("已恢复")
        return True

    if callback.action == "delete":
        await qbit.delete_torrent(callback.payload, delete_files=False)
        await query.edit_message_text(
            format_action_result("已删除任务，保留文件", callback.payload),
            parse_mode=ParseMode.HTML,
        )
        await query.answer("已删除任务")
        return True

    if callback.action == "deletefiles":
        await qbit.delete_torrent(callback.payload, delete_files=True)
        await query.edit_message_text(
            format_action_result("已删除任务和文件", callback.payload),
            parse_mode=ParseMode.HTML,
        )
        await query.answer("已删除任务和文件")
        return True

    if callback.action == "cat":
        payload = parse_category_callback_payload(callback.payload)
        if payload is None:
            await query.answer("这个分类按钮已经过期或不可用。", show_alert=True)
            return True
        choice = await apply_manual_category_choice(
            application,
            qbit,
            torrent_hash=payload.torrent_hash,
            category_index=payload.category_index,
        )
        if not choice:
            await query.answer("这个分类按钮已经过期或不可用。", show_alert=True)
            return True
        await query.edit_message_text(
            "\n".join(
                [
                    "<b>已更新任务分类</b>",
                    f"🗂️ 分类: <code>{escape(choice.label)}</code>",
                    f"🔑 任务 Hash: <code>{escape(choice.torrent_hash)}</code>",
                ]
            ),
            parse_mode=ParseMode.HTML,
        )
        await query.answer(f"已移动到 {choice.label}")
        return True

    return False
