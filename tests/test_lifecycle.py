import asyncio
import unittest
from types import SimpleNamespace

from telegram.error import NetworkError, TelegramError

from app.config import Settings
from app.lifecycle import _watchdog_loop, post_shutdown


class FakeBot:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.calls = 0

    async def get_me(self) -> object:
        self.calls += 1
        if self.error:
            raise self.error
        return object()


class FakeQbit:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.calls = 0
        self.closed = False

    async def get_transfer_info(self) -> object:
        self.calls += 1
        if self.error:
            raise self.error
        return object()

    async def close(self) -> None:
        self.closed = True


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


def make_settings() -> Settings:
    return Settings(
        telegram_bot_token="token",
        telegram_allowed_user_ids=[1],
        qbit_base_url="http://qbit",
        qbit_username="user",
        qbit_password="pass",
        watchdog_interval_seconds=0.01,
        watchdog_max_failures=2,
    )


class WatchdogLoopTests(unittest.IsolatedAsyncioTestCase):
    async def test_qbit_failure_keeps_bot_running(self) -> None:
        app = FakeApplication(
            make_settings(),
            FakeBot(),
            FakeQbit(error=RuntimeError("qbit unavailable")),
        )
        task = asyncio.create_task(_watchdog_loop(app))
        await asyncio.sleep(0.02)
        task.cancel()

        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(app.stop_calls, 0)
        self.assertEqual(app.bot_data["telegram_network_error_times"], [])

    async def test_telegram_network_failure_stops_after_threshold(self) -> None:
        app = FakeApplication(
            make_settings(),
            FakeBot(error=NetworkError("telegram unavailable")),
            FakeQbit(),
        )
        task = asyncio.create_task(_watchdog_loop(app))
        await asyncio.sleep(0.03)

        await task
        self.assertEqual(app.stop_calls, 1)

    async def test_telegram_api_failure_stops_after_threshold(self) -> None:
        app = FakeApplication(
            make_settings(),
            FakeBot(error=TelegramError("telegram rejected request")),
            FakeQbit(),
        )
        task = asyncio.create_task(_watchdog_loop(app))
        await asyncio.sleep(0.03)

        await task
        self.assertEqual(app.stop_calls, 1)


class ShutdownTests(unittest.IsolatedAsyncioTestCase):
    async def test_shutdown_cancels_llm_auto_apply_tasks(self) -> None:
        qbit = FakeQbit()
        app = FakeApplication(make_settings(), FakeBot(), qbit)
        task = asyncio.create_task(asyncio.sleep(60))
        app.bot_data["llm_auto_apply_tasks"] = {task}

        await post_shutdown(app)

        self.assertTrue(task.cancelled())
        self.assertEqual(app.bot_data["llm_auto_apply_tasks"], set())
        self.assertTrue(qbit.closed)
