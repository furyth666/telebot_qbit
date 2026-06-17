from __future__ import annotations

import asyncio
import logging
import re
import time

from telegram import BotCommand
from telegram.error import NetworkError, TelegramError
from telegram.ext import Application

from app.add_flow import MAX_BACKGROUND_FINALIZE_CONCURRENCY
from app.config import Settings
from app.jellyfin_client import JellyfinClient
from app.jav_policy import apply_jav_category_policy
from app.jav_rules import extract_jav_code
from app.jobs import notify_completion_loop
from app.qbit_client import QbitClient, TorrentSummary
from app.runtime_state import persist_state, runtime_context
from app.stash_client import StashClient
from app.state_store import StateStore


_INCOMPLETE_JAV_RECOVERY_WINDOW_SECONDS = 24 * 60 * 60


def _should_recover_incomplete_jav(
    item: TorrentSummary,
    *,
    settings: Settings,
    processed_hashes: set[str],
    jav_pattern: re.Pattern[str],
    now: int,
) -> bool:
    if item.hash in processed_hashes:
        return False
    if item.category != settings.jav_category_name:
        return False
    if not item.added_on:
        return False
    if item.added_on < now - _INCOMPLETE_JAV_RECOVERY_WINDOW_SECONDS:
        return False
    return extract_jav_code(item.name, jav_pattern) is not None


async def recover_incomplete_jav_torrents(application: Application) -> None:
    context = runtime_context(application)
    settings = context.settings
    qbit = context.qbit
    state = context.state
    try:
        torrents = await qbit.list_torrents(filter_name="all")
    except Exception:
        logging.exception("Failed to inspect torrents for incomplete JAV recovery")
        return

    now = int(time.time())
    recovered_count = 0
    for item in torrents:
        if not _should_recover_incomplete_jav(
            item,
            settings=settings,
            processed_hashes=state.jav_processed_hashes,
            jav_pattern=context.jav_pattern,
            now=now,
        ):
            continue
        try:
            result = await apply_jav_category_policy(application, qbit, item.hash)
        except Exception:
            logging.exception(
                "Failed to recover incomplete JAV processing for torrent %s",
                item.hash,
            )
            continue
        recovered_count += 1
        logging.info(
            "Recovered incomplete JAV processing for %s (%s)",
            item.hash,
            result.selection_result.value,
        )

    if recovered_count:
        logging.info("Recovered %s incomplete JAV torrent(s)", recovered_count)


async def watchdog_loop(application: Application) -> None:
    context = runtime_context(application)
    settings: Settings = context.settings
    qbit: QbitClient = context.qbit
    telegram_failures = 0
    qbit_failures = 0

    while True:
        await asyncio.sleep(settings.watchdog_interval_seconds)
        try:
            await application.bot.get_me()
            context.telegram_network_error_times = []
            if telegram_failures:
                logging.info(
                    "Telegram watchdog recovered after %s failed check(s)",
                    telegram_failures,
                )
            telegram_failures = 0
        except asyncio.CancelledError:
            raise
        except TelegramError:
            telegram_failures += 1
            logging.exception(
                "Telegram watchdog health check failed (%s/%s)",
                telegram_failures,
                settings.watchdog_max_failures,
            )
            if telegram_failures >= settings.watchdog_max_failures:
                logging.critical("Telegram watchdog failure limit reached; requesting graceful shutdown")
                application.stop_running()
                return

        try:
            await qbit.get_transfer_info()
        except asyncio.CancelledError:
            raise
        except Exception:
            qbit_failures += 1
            logging.exception(
                "qBittorrent watchdog health check failed (%s); keeping bot running",
                qbit_failures,
            )
        else:
            if qbit_failures:
                logging.info(
                    "qBittorrent watchdog recovered after %s failed check(s)",
                    qbit_failures,
                )
            qbit_failures = 0


async def post_init(application: Application) -> None:
    context = runtime_context(application)
    settings: Settings = context.settings
    context.jav_pattern = re.compile(settings.jav_name_regex)
    context.add_finalize_semaphore = asyncio.Semaphore(
        MAX_BACKGROUND_FINALIZE_CONCURRENCY
    )
    state_store = StateStore(settings.state_file_path)
    state = state_store.load()
    context.state_store = state_store
    context.state = state

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
                BotCommand("stash", "查询 Stash 里的场景"),
                BotCommand("retryjav", "重新执行 JAV 分类和文件筛选"),
            ]
        )
    except NetworkError:
        logging.exception("Failed to set Telegram commands; continuing startup")
    except TelegramError:
        logging.exception("Telegram rejected command setup; continuing startup")

    qbit: QbitClient = context.qbit
    try:
        existing = await qbit.list_torrents(filter_name="completed")
    except Exception:
        logging.exception(
            "Failed to initialize qBittorrent completion baseline; will retry in background"
        )
        context.completion_monitor_initialized = False
    else:
        state.notified_completed_hashes.update(
            item.hash for item in existing
        )
        context.completion_monitor_initialized = True
        await persist_state(application)
    await recover_incomplete_jav_torrents(application)
    context.completion_monitor_task = asyncio.create_task(
        notify_completion_loop(application)
    )
    if settings.watchdog_enabled:
        context.watchdog_task = asyncio.create_task(
            watchdog_loop(application)
        )


async def post_shutdown(application: Application) -> None:
    context = runtime_context(application)
    add_finalize_tasks = list(context.add_finalize_tasks)
    llm_auto_apply_tasks = list(context.llm_auto_apply_tasks)
    tasks = [
        context.completion_monitor_task,
        context.watchdog_task,
        *add_finalize_tasks,
        *llm_auto_apply_tasks,
    ]
    for task in [item for item in tasks if item]:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    context.add_finalize_tasks.clear()
    context.llm_auto_apply_tasks.clear()

    if context.has_persistent_state:
        await persist_state(application)

    if "qbit" in context.data:
        qbit: QbitClient = context.qbit
        await qbit.close()
    if "jellyfin" in context.data:
        jellyfin: JellyfinClient = context.jellyfin
        await jellyfin.close()
    if "stash" in context.data:
        stash: StashClient = context.stash
        await stash.close()
