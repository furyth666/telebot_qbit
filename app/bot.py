from __future__ import annotations

from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)
from telegram.request import HTTPXRequest

from app.config import Settings
from app.handlers import (
    active_handler,
    add_handler,
    delete_files_handler,
    delete_handler,
    detail_handler,
    error_handler,
    help_handler,
    jellyfin_lookup_handler,
    list_handler,
    pause_handler,
    resume_handler,
    retry_jav_handler,
    start_handler,
    status_handler,
    text_link_handler,
    torrent_callback_handler,
)
from app.jellyfin_client import JellyfinClient
from app.lifecycle import post_init, post_shutdown
from app.qbit_client import QbitClient
from app.runtime_state import runtime_context


def _register_handlers(application: Application) -> None:
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


def create_application(settings: Settings) -> Application:
    telegram_request = HTTPXRequest(
        connection_pool_size=settings.telegram_connection_pool_size,
        connect_timeout=settings.telegram_connect_timeout_seconds,
        read_timeout=settings.telegram_read_timeout_seconds,
        write_timeout=settings.telegram_write_timeout_seconds,
        pool_timeout=settings.telegram_pool_timeout_seconds,
    )
    application = (
        Application.builder()
        .token(settings.telegram_bot_token)
        .request(telegram_request)
        .concurrent_updates(settings.telegram_concurrent_updates)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )
    context = runtime_context(application)
    context.settings = settings
    context.qbit = QbitClient(
        settings.qbit_base_url,
        settings.qbit_username,
        settings.qbit_password,
        settings.qbit_api_token,
        timeout=settings.qbit_request_timeout_seconds,
    )
    context.jellyfin = JellyfinClient(
        settings.jellyfin_base_url,
        settings.jellyfin_api_key,
        timeout=settings.jellyfin_request_timeout_seconds,
    )
    _register_handlers(application)
    return application
