from __future__ import annotations

from app.basic_handlers import error_handler, help_handler, start_handler
from app.link_handlers import add_handler, jellyfin_lookup_handler, text_link_handler
from app.torrent_handlers import (
    active_handler,
    delete_files_handler,
    delete_handler,
    detail_handler,
    list_handler,
    pause_handler,
    resume_handler,
    retry_jav_handler,
    status_handler,
    torrent_callback_handler,
)

__all__ = [
    "active_handler",
    "add_handler",
    "delete_files_handler",
    "delete_handler",
    "detail_handler",
    "error_handler",
    "help_handler",
    "jellyfin_lookup_handler",
    "list_handler",
    "pause_handler",
    "resume_handler",
    "retry_jav_handler",
    "start_handler",
    "status_handler",
    "text_link_handler",
    "torrent_callback_handler",
]
