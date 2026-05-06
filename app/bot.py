from __future__ import annotations

import asyncio
import io
import logging
import os
import re
from html import escape

import httpx
from telegram import (
    BotCommand,
    InlineKeyboardMarkup,
    InputFile,
    Update,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from app.add_links import (
    AddContext,
    _add_torrent_links,
    _extract_torrent_links,
    _format_add_batch_reply,
)
from app.config import Settings
from app.formatters import (
    _build_list_keyboard,
    _fmt_bytes,
    _fmt_large_file_threshold,
    _fmt_speed,
    _format_action_result,
    _format_jellyfin_caption,
    _format_torrent_detail,
    _format_torrent_line,
    _format_torrent_overview,
    _fmt_torrent_caption,
    _short_hash,
    filter_name_to_view,
)
from app.jav_rules import (
    _extract_jav_lookup_code,
    _is_jav_title,
)
from app.jobs import (
    _apply_jav_file_selection,
    _background_finalize_torrent,
    _notify_completion_loop,
)
from app.jellyfin_client import JellyfinClient, JellyfinItem
from app.qbit_client import QbitClient
from app.runtime_state import _get_jav_pattern, _get_state, _persist_state
from app.state_store import StateStore


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


def _pick_best_jellyfin_match(code: str, items: list[JellyfinItem]) -> JellyfinItem:
    code_lower = code.lower()
    scored = []
    for item in items:
        name = item.name.lower()
        path = item.path.lower()
        score = 0
        if code_lower == name:
            score += 4
        if code_lower in name:
            score += 3
        if code_lower in path:
            score += 2
        scored.append((score, item))
    scored.sort(key=lambda entry: entry[0], reverse=True)
    return scored[0][1]


def _start_add_background_tasks(
    application: Application,
    qbit: QbitClient,
    contexts: list[AddContext],
    chat_id: int,
) -> None:
    for add_context in contexts:
        application.create_task(
            _background_finalize_torrent(
                application,
                qbit,
                add_context,
                chat_id,
            )
        )


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


def _get_hash_argument(context: ContextTypes.DEFAULT_TYPE) -> str | None:
    if not context.args:
        return None
    return context.args[0].strip()


async def _resolve_hash_or_reply(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> str | None:
    torrent_hash = _get_hash_argument(context)
    if not torrent_hash:
        return None
    qbit: QbitClient = context.application.bot_data["qbit"]
    try:
        return await qbit.resolve_hash(torrent_hash)
    except ValueError as exc:
        await update.message.reply_text(str(exc))
        return None


async def _reply_qbit_action_error(update: Update, error: Exception) -> None:
    if isinstance(error, httpx.HTTPStatusError):
        await update.message.reply_text(
            f"操作失败：qBittorrent 返回 {error.response.status_code}。"
        )
        return
    await update.message.reply_text(f"操作失败：{error}")


async def _callback_action_error(query, error: Exception) -> None:
    if isinstance(error, httpx.HTTPStatusError):
        await query.answer(f"qBittorrent 返回 {error.response.status_code}", show_alert=True)
        return
    await query.answer(str(error), show_alert=True)


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
    full_hash = await _resolve_hash_or_reply(update, context)
    if not full_hash:
        return
    try:
        await qbit.pause_torrent(full_hash)
    except Exception as exc:
        await _reply_qbit_action_error(update, exc)
        return
    await update.message.reply_text(
        _format_action_result("已暂停任务", full_hash),
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
    full_hash = await _resolve_hash_or_reply(update, context)
    if not full_hash:
        return
    try:
        await qbit.resume_torrent(full_hash)
    except Exception as exc:
        await _reply_qbit_action_error(update, exc)
        return
    await update.message.reply_text(
        _format_action_result("已恢复任务", full_hash),
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
    full_hash = await _resolve_hash_or_reply(update, context)
    if not full_hash:
        return
    try:
        await qbit.delete_torrent(full_hash, delete_files=False)
    except Exception as exc:
        await _reply_qbit_action_error(update, exc)
        return
    await update.message.reply_text(
        _format_action_result("已删除任务，保留文件", full_hash),
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
    full_hash = await _resolve_hash_or_reply(update, context)
    if not full_hash:
        return
    try:
        await qbit.delete_torrent(full_hash, delete_files=True)
    except Exception as exc:
        await _reply_qbit_action_error(update, exc)
        return
    await update.message.reply_text(
        _format_action_result("已删除任务和文件", full_hash),
        parse_mode=ParseMode.HTML,
    )


async def torrent_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_allowed_user(update, context):
        return
    query = update.callback_query
    if not query or not query.data:
        return
    try:
        _, action, view, payload = query.data.split(":", 3)
    except ValueError:
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
    except Exception as exc:
        await _callback_action_error(query, exc)


async def add_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_allowed_user(update, context):
        return
    if not context.args:
        await update.message.reply_text("用法: /add <一个或多个 magnet/torrent 链接>")
        return
    text = " ".join(context.args).strip()
    links = _extract_torrent_links(text)
    if not links:
        await update.message.reply_text("没有识别到可添加的下载链接。")
        return

    qbit: QbitClient = context.application.bot_data["qbit"]
    chat = update.effective_chat
    if not chat:
        return
    result = await _add_torrent_links(context.application, qbit, links)
    await update.message.reply_text(
        _format_add_batch_reply(
            result,
            auto_detected=False,
            settings=context.application.bot_data["settings"],
        ),
        parse_mode=ParseMode.HTML,
    )
    _start_add_background_tasks(context.application, qbit, result.contexts, chat.id)


async def _reply_jellyfin_lookup(
    update: Update, context: ContextTypes.DEFAULT_TYPE, code: str
) -> None:
    jellyfin: JellyfinClient = context.application.bot_data["jellyfin"]
    settings: Settings = context.application.bot_data["settings"]
    if not jellyfin.enabled:
        await update.effective_message.reply_text("Jellyfin 查询未启用。")
        return

    items = await jellyfin.find_by_code(code)
    if not items:
        await update.effective_message.reply_text(
            (
                "<b>🔎 Jellyfin 未找到匹配</b>\n"
                f"🏷️ 番号: <code>{escape(code)}</code>"
            ),
            parse_mode=ParseMode.HTML,
        )
        return

    first_item = _pick_best_jellyfin_match(code, items)
    public_base_url = settings.jellyfin_public_base_url or settings.jellyfin_base_url
    caption = _format_jellyfin_caption(
        code,
        first_item,
        len(items),
        public_base_url=public_base_url,
    )
    image_bytes = await jellyfin.get_primary_image_bytes(first_item.item_id)

    if image_bytes:
        await update.effective_message.reply_photo(
            photo=InputFile(io.BytesIO(image_bytes), filename=f"{code}.jpg"),
            caption=caption,
            parse_mode=ParseMode.HTML,
        )
        return

    await update.effective_message.reply_text(caption, parse_mode=ParseMode.HTML)


async def jellyfin_lookup_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    if not await _require_allowed_user(update, context):
        return
    if not context.args:
        await update.message.reply_text("用法: /jav <番号>")
        return
    code = _extract_jav_lookup_code(
        " ".join(context.args),
        _get_jav_pattern(context.application),
    )
    if not code:
        await update.message.reply_text("没有识别到有效番号，例如: /jav PRWF-010")
        return
    await _reply_jellyfin_lookup(update, context, code)


async def text_link_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_allowed_user(update, context):
        return
    message = update.effective_message
    text = message.text if message else None
    if not text:
        return

    links = _extract_torrent_links(text)
    if not links:
        code = _extract_jav_lookup_code(text, _get_jav_pattern(context.application))
        if code:
            await _reply_jellyfin_lookup(update, context, code)
        return

    qbit: QbitClient = context.application.bot_data["qbit"]
    chat = update.effective_chat
    if not chat:
        return
    result = await _add_torrent_links(context.application, qbit, links)
    await message.reply_text(
        _format_add_batch_reply(
            result,
            auto_detected=True,
            settings=context.application.bot_data["settings"],
        ),
        parse_mode=ParseMode.HTML,
    )
    _start_add_background_tasks(context.application, qbit, result.contexts, chat.id)


async def error_handler(_: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logging.exception("Unhandled bot error", exc_info=context.error)


async def _watchdog_loop(application: Application) -> None:
    settings: Settings = application.bot_data["settings"]
    qbit: QbitClient = application.bot_data["qbit"]
    failures = 0

    while True:
        await asyncio.sleep(settings.watchdog_interval_seconds)
        try:
            await application.bot.get_me()
            await qbit.get_transfer_info()
            if failures:
                logging.info("Watchdog recovered after %s failed check(s)", failures)
            failures = 0
        except asyncio.CancelledError:
            raise
        except Exception:
            failures += 1
            logging.exception(
                "Watchdog health check failed (%s/%s)",
                failures,
                settings.watchdog_max_failures,
            )
            if failures >= settings.watchdog_max_failures:
                logging.critical("Watchdog failure limit reached; exiting for Docker restart")
                os._exit(1)


async def post_init(application: Application) -> None:
    settings: Settings = application.bot_data["settings"]
    await application.bot.set_my_commands(
        [
            BotCommand("start", "显示欢迎信息和命令说明"),
            BotCommand("help", "查看命令帮助"),
            BotCommand("status", "查看 qBittorrent 整体状态"),
            BotCommand("list", "查看最近 10 个任务"),
            BotCommand("active", "查看活动任务"),
            BotCommand("detail", "查看任务详情，用法: /detail <hash>"),
            BotCommand("pause", "暂停任务，用法: /pause <hash>"),
            BotCommand("resume", "恢复任务，用法: /resume <hash>"),
            BotCommand("delete", "删除任务并保留文件"),
            BotCommand("deletefiles", "删除任务和文件"),
            BotCommand("add", "添加磁力链接或 torrent 链接"),
            BotCommand("jav", "查询 Jellyfin 里的同番号影片"),
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
    if settings.watchdog_enabled:
        application.bot_data["watchdog_task"] = asyncio.create_task(
            _watchdog_loop(application)
        )


async def post_shutdown(application: Application) -> None:
    tasks = [
        application.bot_data.get("completion_monitor_task"),
        application.bot_data.get("watchdog_task"),
    ]
    for task in [item for item in tasks if item]:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    _persist_state(application)

    qbit: QbitClient = application.bot_data["qbit"]
    await qbit.close()
    jellyfin: JellyfinClient = application.bot_data["jellyfin"]
    await jellyfin.close()


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
    application.bot_data["jellyfin"] = JellyfinClient(
        settings.jellyfin_base_url,
        settings.jellyfin_api_key,
    )

    application.add_handler(CommandHandler("start", start_handler))
    application.add_handler(CommandHandler("help", help_handler))
    application.add_handler(CommandHandler("status", status_handler))
    application.add_handler(CommandHandler("list", list_handler))
    application.add_handler(CommandHandler("active", active_handler))
    application.add_handler(CommandHandler("detail", detail_handler))
    application.add_handler(CommandHandler("pause", pause_handler))
    application.add_handler(CommandHandler("resume", resume_handler))
    application.add_handler(CommandHandler("delete", delete_handler))
    application.add_handler(CommandHandler("deletefiles", delete_files_handler))
    application.add_handler(CommandHandler("add", add_handler))
    application.add_handler(CommandHandler("jav", jellyfin_lookup_handler))
    application.add_handler(CommandHandler("retryjav", retry_jav_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_link_handler))
    application.add_handler(CallbackQueryHandler(torrent_callback_handler, pattern=r"^tor:"))
    application.add_error_handler(error_handler)
    return application
