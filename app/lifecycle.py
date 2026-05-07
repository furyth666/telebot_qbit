from __future__ import annotations

import asyncio
import logging
import re

from telegram import BotCommand
from telegram.error import NetworkError, TelegramError
from telegram.ext import Application

from app.config import Settings
from app.jellyfin_client import JellyfinClient
from app.jobs import _notify_completion_loop
from app.qbit_client import QbitClient
from app.runtime_state import _persist_state
from app.state_store import StateStore


async def _watchdog_loop(application: Application) -> None:
    settings: Settings = application.bot_data["settings"]
    qbit: QbitClient = application.bot_data["qbit"]
    failures = 0

    while True:
        await asyncio.sleep(settings.watchdog_interval_seconds)
        try:
            await application.bot.get_me()
            await qbit.get_transfer_info()
            application.bot_data["telegram_network_error_times"] = []
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
                logging.critical("Watchdog failure limit reached; requesting graceful shutdown")
                application.stop_running()
                return


async def post_init(application: Application) -> None:
    settings: Settings = application.bot_data["settings"]
    try:
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
    except NetworkError:
        logging.exception("Failed to set Telegram commands; continuing startup")
    except TelegramError:
        logging.exception("Telegram rejected command setup; continuing startup")
    application.bot_data["jav_name_pattern"] = re.compile(settings.jav_name_regex)
    state_store = StateStore(settings.state_file_path)
    state = state_store.load()
    application.bot_data["state_store"] = state_store
    application.bot_data["bot_state"] = state

    qbit: QbitClient = application.bot_data["qbit"]
    try:
        existing = await qbit.list_torrents(filter_name="completed")
    except Exception:
        logging.exception(
            "Failed to initialize qBittorrent completion baseline; will retry in background"
        )
        application.bot_data["completion_monitor_initialized"] = False
    else:
        state.notified_completed_hashes.update(
            item.hash for item in existing
        )
        application.bot_data["completion_monitor_initialized"] = True
        await _persist_state(application)
    application.bot_data["completion_monitor_task"] = asyncio.create_task(
        _notify_completion_loop(application)
    )
    if settings.watchdog_enabled:
        application.bot_data["watchdog_task"] = asyncio.create_task(
            _watchdog_loop(application)
        )


async def post_shutdown(application: Application) -> None:
    add_finalize_tasks = list(application.bot_data.get("add_finalize_tasks", set()))
    tasks = [
        application.bot_data.get("completion_monitor_task"),
        application.bot_data.get("watchdog_task"),
        *add_finalize_tasks,
    ]
    for task in [item for item in tasks if item]:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    application.bot_data.get("add_finalize_tasks", set()).clear()

    await _persist_state(application)

    qbit: QbitClient = application.bot_data["qbit"]
    await qbit.close()
    jellyfin: JellyfinClient = application.bot_data["jellyfin"]
    await jellyfin.close()
