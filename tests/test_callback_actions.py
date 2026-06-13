import unittest

from telegram.constants import ParseMode

from app.callback_actions import handle_torrent_callback_action
from app.callback_data import TorrentCallback
from app.runtime_state import runtime_context


class FakeApplication:
    def __init__(self) -> None:
        self.bot_data = {}


class FakeQbit:
    def __init__(self) -> None:
        self.deleted: list[tuple[str, bool]] = []
        self.categories: list[tuple[str, str]] = []

    async def delete_torrent(self, torrent_hash: str, *, delete_files: bool = False) -> None:
        self.deleted.append((torrent_hash, delete_files))

    async def set_category(self, torrent_hash: str, category: str) -> None:
        self.categories.append((torrent_hash, category))


class FakeQuery:
    def __init__(self) -> None:
        self.edited_messages: list[dict] = []
        self.answers: list[tuple[str | None, bool]] = []

    async def edit_message_text(self, text: str, **kwargs) -> None:
        self.edited_messages.append({"text": text, **kwargs})

    async def answer(self, text: str | None = None, *, show_alert: bool = False) -> None:
        self.answers.append((text, show_alert))


class CallbackActionTests(unittest.IsolatedAsyncioTestCase):
    async def test_delete_action_deletes_torrent_and_edits_message(self) -> None:
        app = FakeApplication()
        qbit = FakeQbit()
        runtime_context(app).qbit = qbit
        query = FakeQuery()

        handled = await handle_torrent_callback_action(
            app,
            query,
            TorrentCallback(action="delete", view="all", payload="abc123"),
        )

        self.assertTrue(handled)
        self.assertEqual(qbit.deleted, [("abc123", False)])
        self.assertIn("已删除任务，保留文件", query.edited_messages[0]["text"])
        self.assertEqual(query.edited_messages[0]["parse_mode"], ParseMode.HTML)
        self.assertEqual(query.answers, [("已删除任务", False)])

    async def test_deletefiles_action_deletes_torrent_files(self) -> None:
        app = FakeApplication()
        qbit = FakeQbit()
        runtime_context(app).qbit = qbit
        query = FakeQuery()

        handled = await handle_torrent_callback_action(
            app,
            query,
            TorrentCallback(action="deletefiles", view="all", payload="abc123"),
        )

        self.assertTrue(handled)
        self.assertEqual(qbit.deleted, [("abc123", True)])
        self.assertIn("已删除任务和文件", query.edited_messages[0]["text"])
        self.assertEqual(query.answers, [("已删除任务和文件", False)])

    async def test_category_action_applies_pending_choice(self) -> None:
        app = FakeApplication()
        qbit = FakeQbit()
        runtime = runtime_context(app)
        runtime.qbit = qbit
        runtime.pending_category_choices["abc123"] = ["", "movies"]
        runtime.prompted_category_hashes.add("abc123")
        query = FakeQuery()

        handled = await handle_torrent_callback_action(
            app,
            query,
            TorrentCallback(action="cat", view="all", payload="abc123:1"),
        )

        self.assertTrue(handled)
        self.assertEqual(qbit.categories, [("abc123", "movies")])
        self.assertEqual(runtime.pending_category_choices, {})
        self.assertEqual(runtime.prompted_category_hashes, set())
        self.assertIn("已更新任务分类", query.edited_messages[0]["text"])
        self.assertIn("movies", query.edited_messages[0]["text"])
        self.assertEqual(query.answers, [("已移动到 movies", False)])

    async def test_category_action_answers_when_payload_is_stale(self) -> None:
        app = FakeApplication()
        runtime_context(app).qbit = FakeQbit()
        query = FakeQuery()

        handled = await handle_torrent_callback_action(
            app,
            query,
            TorrentCallback(action="cat", view="all", payload="abc123:bad"),
        )

        self.assertTrue(handled)
        self.assertEqual(query.edited_messages, [])
        self.assertEqual(query.answers, [("这个分类按钮已经过期或不可用。", True)])

    async def test_unknown_action_returns_false(self) -> None:
        app = FakeApplication()
        runtime_context(app).qbit = FakeQbit()
        query = FakeQuery()

        handled = await handle_torrent_callback_action(
            app,
            query,
            TorrentCallback(action="unknown", view="all", payload="abc123"),
        )

        self.assertFalse(handled)
        self.assertEqual(query.edited_messages, [])
        self.assertEqual(query.answers, [])
