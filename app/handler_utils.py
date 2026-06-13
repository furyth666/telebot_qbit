from __future__ import annotations

import httpx
from telegram import Update
from telegram.ext import ContextTypes

from app.config import Settings
from app.qbit_client import QbitClient
from app.runtime_state import runtime_context

__all__ = [
    "callback_action_error",
    "get_hash_argument",
    "reply_qbit_action_error",
    "require_allowed_user",
    "resolve_hash_or_reply",
]


async def require_allowed_user(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> bool:
    settings: Settings = runtime_context(context.application).settings
    user = update.effective_user
    if not user or user.id not in settings.telegram_allowed_user_ids:
        if update.effective_message:
            await update.effective_message.reply_text("无权限使用这个 bot。")
        return False
    return True


def get_hash_argument(context: ContextTypes.DEFAULT_TYPE) -> str | None:
    if not context.args:
        return None
    return context.args[0].strip()


async def resolve_hash_or_reply(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> str | None:
    torrent_hash = get_hash_argument(context)
    if not torrent_hash:
        return None
    qbit: QbitClient = runtime_context(context.application).qbit
    try:
        return await qbit.resolve_hash(torrent_hash)
    except ValueError as exc:
        await update.message.reply_text(str(exc))
        return None
    except Exception as exc:
        await reply_qbit_action_error(update, exc)
        return None


async def reply_qbit_action_error(update: Update, error: Exception) -> None:
    if isinstance(error, httpx.HTTPStatusError):
        await update.message.reply_text(
            f"操作失败：qBittorrent 返回 {error.response.status_code}。"
        )
        return
    await update.message.reply_text(f"操作失败：{error}")


async def callback_action_error(query, error: Exception) -> None:
    if isinstance(error, httpx.HTTPStatusError):
        await query.answer(f"qBittorrent 返回 {error.response.status_code}", show_alert=True)
        return
    await query.answer(str(error), show_alert=True)
