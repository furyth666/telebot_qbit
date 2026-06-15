import asyncio
import re
import tempfile
import unittest
from unittest.mock import patch

from telegram.error import NetworkError, TelegramError

from app.config import Settings
from app.lifecycle import (
    post_init,
    post_shutdown,
    recover_incomplete_jav_torrents,
    watchdog_loop,
)
from app.qbit_client import TorrentFile, TorrentSummary
from app.runtime_state import runtime_context
from app.state_store import StateStore


class FakeBot:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.calls = 0
        self.commands = None

    async def get_me(self) -> object:
        self.calls += 1
        if self.error:
            raise self.error
        return object()

    async def set_my_commands(self, commands) -> None:
        self.commands = commands


class FakeQbit:
    def __init__(
        self,
        *,
        error: Exception | None = None,
        completed: list[TorrentSummary] | None = None,
        torrents: list[TorrentSummary] | None = None,
        files: list[TorrentFile] | None = None,
        list_error: Exception | None = None,
    ) -> None:
        self.error = error
        self.completed = completed or []
        self.torrents = torrents or []
        self.files = files or []
        self.list_error = list_error
        self.calls = 0
        self.closed = False
        self.created_categories: list[str] = []
        self.set_categories: list[tuple[str, str]] = []
        self.file_priorities: list[tuple[str, list[int], int]] = []

    async def get_transfer_info(self) -> object:
        self.calls += 1
        if self.error:
            raise self.error
        return object()

    async def close(self) -> None:
        self.closed = True

    async def list_torrents(self, *, filter_name: str = "all") -> list[TorrentSummary]:
        if self.list_error:
            raise self.list_error
        if filter_name == "completed":
            return self.completed
        if filter_name == "all":
            return self.torrents
        return []

    async def create_category(self, category: str) -> None:
        self.created_categories.append(category)

    async def set_category(self, torrent_hash: str, category: str) -> None:
        self.set_categories.append((torrent_hash, category))

    async def get_torrent_files(self, torrent_hash: str) -> list[TorrentFile]:
        return self.files

    async def set_file_priority(
        self,
        torrent_hash: str,
        file_indexes: list[int],
        priority: int,
    ) -> None:
        self.file_priorities.append((torrent_hash, file_indexes, priority))


class FakeApplication:
    def __init__(self, settings: Settings, bot: FakeBot, qbit: FakeQbit) -> None:
        self.bot = bot
        self.bot_data = {
            "settings": settings,
            "qbit": qbit,
            "telegram_network_error_times": [1.0],
        }
        self.stop_calls = 0

    def stop_running(self) -> None:
        self.stop_calls += 1


def make_settings(**overrides) -> Settings:
    values = {
        "telegram_bot_token": "token",
        "telegram_allowed_user_ids": [1],
        "qbit_base_url": "http://qbit",
        "qbit_username": "user",
        "qbit_password": "pass",
        "watchdog_interval_seconds": 0.01,
        "watchdog_max_failures": 2,
    }
    values.update(overrides)
    return Settings(**values)


def make_torrent(
    torrent_hash: str = "a" * 40,
    *,
    name: str = "Completed Torrent",
    category: str = "",
    added_on: int = 50,
) -> TorrentSummary:
    return TorrentSummary(
        name=name,
        hash=torrent_hash,
        category=category,
        state="uploading",
        progress=1,
        dlspeed=0,
        upspeed=0,
        eta=0,
        size=0,
        completion_on=100,
        added_on=added_on,
    )


async def wait_forever(_: object) -> None:
    await asyncio.sleep(60)


class WatchdogLoopTests(unittest.IsolatedAsyncioTestCase):
    async def test_qbit_failure_keeps_bot_running(self) -> None:
        app = FakeApplication(
            make_settings(),
            FakeBot(),
            FakeQbit(error=RuntimeError("qbit unavailable")),
        )
        with self.assertLogs(level="ERROR") as logs:
            task = asyncio.create_task(watchdog_loop(app))
            await asyncio.sleep(0.02)
            task.cancel()

            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(app.stop_calls, 0)
        self.assertEqual(runtime_context(app).telegram_network_error_times, [])
        self.assertIn("qBittorrent watchdog health check failed", "\n".join(logs.output))

    async def test_telegram_network_failure_stops_after_threshold(self) -> None:
        app = FakeApplication(
            make_settings(),
            FakeBot(error=NetworkError("telegram unavailable")),
            FakeQbit(),
        )
        with self.assertLogs(level="ERROR") as logs:
            task = asyncio.create_task(watchdog_loop(app))
            await asyncio.sleep(0.03)

            await task
        self.assertEqual(app.stop_calls, 1)
        self.assertIn("Telegram watchdog health check failed", "\n".join(logs.output))

    async def test_telegram_api_failure_stops_after_threshold(self) -> None:
        app = FakeApplication(
            make_settings(),
            FakeBot(error=TelegramError("telegram rejected request")),
            FakeQbit(),
        )
        with self.assertLogs(level="ERROR") as logs:
            task = asyncio.create_task(watchdog_loop(app))
            await asyncio.sleep(0.03)

            await task
        self.assertEqual(app.stop_calls, 1)
        self.assertIn("Telegram watchdog health check failed", "\n".join(logs.output))


class StartupTests(unittest.IsolatedAsyncioTestCase):
    async def test_recover_incomplete_jav_torrents_finishes_recent_jav_task(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            torrent_hash = "a" * 40
            settings = make_settings(
                state_file_path=f"{temp_dir}/bot_state.sqlite3",
            )
            qbit = FakeQbit(
                torrents=[
                    make_torrent(
                        torrent_hash,
                        name="[javdb.com]PRWF-012.torrent",
                        category="JAV",
                        added_on=9999999999,
                    )
                ],
                files=[
                    TorrentFile(index=0, name="movie.mp4", size=2 * 1024**3, priority=1),
                    TorrentFile(index=1, name="sample.mp4", size=100 * 1024**2, priority=1),
                ],
            )
            app = FakeApplication(settings, FakeBot(), qbit)
            context = runtime_context(app)
            context.jav_pattern = re.compile(settings.jav_name_regex)

            state_store = StateStore(settings.state_file_path)
            context.state_store = state_store
            context.state = state_store.load()

            await recover_incomplete_jav_torrents(app)

            self.assertEqual(qbit.set_categories, [(torrent_hash, "JAV")])
            self.assertEqual(
                qbit.file_priorities,
                [
                    (torrent_hash, [0], 1),
                    (torrent_hash, [1], 0),
                ],
            )
            self.assertIn(torrent_hash, context.state.jav_processed_hashes)

    async def test_post_init_sets_runtime_state_and_completion_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = make_settings(
                watchdog_enabled=False,
                state_file_path=f"{temp_dir}/bot_state.sqlite3",
            )
            completed = make_torrent("b" * 40)
            app = FakeApplication(settings, FakeBot(), FakeQbit(completed=[completed]))

            with patch("app.lifecycle.notify_completion_loop", wait_forever):
                await post_init(app)

            context = runtime_context(app)
            self.assertIsNotNone(context.jav_pattern.search("SSIS-123"))
            self.assertIn(completed.hash, context.state.notified_completed_hashes)
            self.assertTrue(context.completion_monitor_initialized)
            self.assertIsNotNone(context.add_finalize_semaphore)
            self.assertIsNotNone(context.completion_monitor_task)
            self.assertIsNone(context.watchdog_task)
            self.assertIsNotNone(app.bot.commands)

            await post_shutdown(app)
            self.assertEqual(context.add_finalize_tasks, set())
            self.assertEqual(context.llm_auto_apply_tasks, set())

    async def test_post_init_marks_completion_monitor_uninitialized_on_qbit_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = make_settings(
                watchdog_enabled=False,
                state_file_path=f"{temp_dir}/bot_state.sqlite3",
            )
            app = FakeApplication(
                settings,
                FakeBot(),
                FakeQbit(list_error=RuntimeError("qbit unavailable")),
            )

            with self.assertLogs(level="ERROR") as logs:
                with patch("app.lifecycle.notify_completion_loop", wait_forever):
                    await post_init(app)

            context = runtime_context(app)
            self.assertFalse(context.completion_monitor_initialized)
            self.assertEqual(context.state.notified_completed_hashes, set())
            self.assertIsNotNone(context.completion_monitor_task)
            self.assertIn("Failed to initialize qBittorrent completion baseline", "\n".join(logs.output))

            await post_shutdown(app)


class ShutdownTests(unittest.IsolatedAsyncioTestCase):
    async def test_shutdown_cancels_llm_auto_apply_tasks(self) -> None:
        qbit = FakeQbit()
        app = FakeApplication(make_settings(), FakeBot(), qbit)
        task = asyncio.create_task(asyncio.sleep(60))
        runtime_context(app).llm_auto_apply_tasks.add(task)

        await post_shutdown(app)

        self.assertTrue(task.cancelled())
        self.assertEqual(runtime_context(app).llm_auto_apply_tasks, set())
        self.assertTrue(qbit.closed)
