import unittest

from app.add_flow import finalize_added_torrents_batch, submit_add_links_from_text
from app.add_types import AddContext
from app.config import Settings
from app.runtime_state import runtime_context


class FakeTask:
    def __init__(self) -> None:
        self.callbacks = []

    def add_done_callback(self, callback) -> None:
        self.callbacks.append(callback)


class FakeApplication:
    def __init__(self) -> None:
        self.created_tasks = []
        self.bot_data = {
            "settings": Settings(
                telegram_bot_token="token",
                telegram_allowed_user_ids=[1],
                qbit_base_url="http://qbit",
                qbit_username="user",
                qbit_password="pass",
            ),
        }

    def create_task(self, coro) -> FakeTask:
        coro.close()
        task = FakeTask()
        self.created_tasks.append(task)
        return task


class FakeQbit:
    def __init__(self) -> None:
        self.added_urls: list[tuple[str, int | None]] = []

    async def list_torrents(self, *, filter_name: str = "all") -> list:
        return []

    async def add_torrent_url_with_options(
        self,
        url: str,
        *,
        upload_limit: int | None = None,
        category: str | None = None,
    ) -> None:
        self.added_urls.append((url, upload_limit))


class AddFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_submit_add_links_from_text_returns_none_without_links(self) -> None:
        app = FakeApplication()
        runtime_context(app).qbit = FakeQbit()

        result = await submit_add_links_from_text(
            app,
            "not a torrent link",
            auto_detected=True,
            chat_id=1,
        )

        self.assertIsNone(result)
        self.assertEqual(app.created_tasks, [])

    async def test_submit_add_links_from_text_adds_and_schedules_finalize(self) -> None:
        app = FakeApplication()
        qbit = FakeQbit()
        runtime_context(app).qbit = qbit

        result = await submit_add_links_from_text(
            app,
            "magnet:?xt=urn:btih:" + "a" * 40 + "&dn=The.Show.S01E01",
            auto_detected=True,
            chat_id=1,
        )

        self.assertIsNotNone(result)
        self.assertEqual(result.batch.success_count, 1)
        self.assertEqual(result.batch.magnet_count, 1)
        self.assertIn("已自动识别并添加下载链接", result.reply_text)
        self.assertEqual(qbit.added_urls[0][1], 30 * 1024)
        self.assertEqual(len(app.created_tasks), 1)
        self.assertEqual(runtime_context(app).add_finalize_tasks, set(app.created_tasks))

    async def test_finalize_batch_requires_initialized_semaphore(self) -> None:
        app = FakeApplication()

        with self.assertRaisesRegex(RuntimeError, "Finalize semaphore"):
            await finalize_added_torrents_batch(
                app,
                FakeQbit(),
                [
                    AddContext(
                        known_hashes=set(),
                        started_at=100,
                        name_hint=None,
                        is_magnet=True,
                        expected_hashes={"a" * 40},
                    )
                ],
                chat_id=1,
            )
