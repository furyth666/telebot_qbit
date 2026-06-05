import re
import unittest

from telegram.constants import ParseMode
from telegram.error import BadRequest

from app.config import Settings
from app.jav_patterns import DEFAULT_JAV_NAME_REGEX
from app.jobs import (
    _background_finalize_torrent,
    _category_choice_keyboard,
    _category_choices,
)
from app.add_links import AddContext
from app.jellyfin_client import JellyfinItem
from app.qbit_client import TorrentCategory, TorrentFile, TorrentSummary
from app.state_store import BotState


def _torrent(name: str, torrent_hash: str = "a" * 40) -> TorrentSummary:
    return TorrentSummary(
        name=name,
        hash=torrent_hash,
        category="",
        state="downloading",
        progress=0,
        dlspeed=0,
        upspeed=0,
        eta=0,
        size=0,
        completion_on=0,
        added_on=100,
    )


class FakeBot:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def send_message(self, **kwargs) -> None:
        if kwargs.get("parse_mode") == ParseMode.HTML and "<hash>" in kwargs.get("text", ""):
            raise BadRequest("Can't parse entities: unsupported start tag \"hash\"")
        self.messages.append(kwargs)


class FakeStateStore:
    def __init__(self) -> None:
        self.save_calls = 0

    async def save_async(self, state: BotState) -> None:
        self.save_calls += 1


class FakeJellyfin:
    def __init__(
        self,
        items: list[JellyfinItem] | None = None,
        *,
        enabled: bool = False,
    ) -> None:
        self.items = items or []
        self._enabled = enabled
        self.queries: list[str] = []

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def find_by_code(self, code: str) -> list[JellyfinItem]:
        self.queries.append(code)
        return self.items


class FakeQbit:
    def __init__(
        self,
        torrents: list[TorrentSummary],
        *,
        files: list[TorrentFile] | None = None,
        fail_small_file_priority: bool = False,
    ) -> None:
        self.torrents = torrents
        self.created_categories: list[str] = []
        self.set_categories: list[tuple[str, str]] = []
        self.deleted_torrents: list[tuple[str, bool]] = []
        self.files = files or [
            TorrentFile(index=0, name="movie.mp4", size=2 * 1024 * 1024 * 1024, priority=1)
        ]
        self.fail_small_file_priority = fail_small_file_priority

    async def list_torrents(self, *, filter_name: str = "all") -> list[TorrentSummary]:
        return self.torrents

    async def list_categories(self) -> list[TorrentCategory]:
        return [
            TorrentCategory(name="JAV", save_path="/downloads/jav"),
            TorrentCategory(name="TV", save_path="/downloads/tv"),
        ]

    async def create_category(self, category: str) -> None:
        self.created_categories.append(category)

    async def set_category(self, torrent_hash: str, category: str) -> None:
        self.set_categories.append((torrent_hash, category))

    async def delete_torrent(self, torrent_hash: str, *, delete_files: bool) -> None:
        self.deleted_torrents.append((torrent_hash, delete_files))

    async def get_torrent_files(self, torrent_hash: str) -> list[TorrentFile]:
        return self.files

    async def set_file_priority(
        self,
        torrent_hash: str,
        file_indexes: list[int],
        priority: int,
    ) -> None:
        if self.fail_small_file_priority and priority == 0:
            raise RuntimeError("file metadata changed")


class FakeApplication:
    def __init__(
        self,
        *,
        jellyfin: FakeJellyfin | None = None,
        jellyfin_duplicate_delete_enabled: bool = False,
    ) -> None:
        self.bot = FakeBot()
        self.state_store = FakeStateStore()
        self.bot_data = {
            "settings": Settings(
                telegram_bot_token="token",
                telegram_allowed_user_ids=[1],
                qbit_base_url="http://qbit",
                qbit_username="user",
                qbit_password="pass",
                jellyfin_duplicate_delete_enabled=jellyfin_duplicate_delete_enabled,
            ),
            "jav_name_pattern": re.compile(DEFAULT_JAV_NAME_REGEX),
            "bot_state": BotState(),
            "state_store": self.state_store,
            "jellyfin": jellyfin or FakeJellyfin(),
        }


def _add_context() -> AddContext:
    return AddContext(
        known_hashes=set(),
        started_at=100,
        name_hint=None,
        is_magnet=True,
    )


def _jellyfin_item(code: str = "SSIS-123", *, path_suffix: str = "") -> JellyfinItem:
    return JellyfinItem(
        item_id="item-1",
        server_id="server-1",
        name=code,
        path=f"/media/{code}{path_suffix}.mp4",
        overview="",
        production_year=None,
        premiere_date="",
        actors=(),
    )


class CategoryPromptTests(unittest.TestCase):
    def test_category_choices_include_uncategorized_first(self) -> None:
        choices = _category_choices(
            [
                TorrentCategory(name="JAV", save_path="/downloads/jav"),
                TorrentCategory(name="TV", save_path="/downloads/tv"),
            ]
        )

        self.assertEqual(choices, ["", "JAV", "TV"])

    def test_category_keyboard_uses_hash_and_index_payloads(self) -> None:
        torrent_hash = "a" * 40
        keyboard = _category_choice_keyboard(torrent_hash, ["", "JAV", "TV"])
        payloads = [
            button.callback_data
            for row in keyboard.inline_keyboard
            for button in row
        ]

        self.assertEqual(
            payloads,
            [
                f"tor:cat:all:{torrent_hash}:0",
                f"tor:cat:all:{torrent_hash}:1",
                f"tor:cat:all:{torrent_hash}:2",
            ],
        )
        self.assertTrue(all(len(item.encode("utf-8")) <= 64 for item in payloads))


class FinalizeTorrentTests(unittest.IsolatedAsyncioTestCase):
    async def test_jav_torrent_is_processed_without_category_prompt(self) -> None:
        app = FakeApplication()
        qbit = FakeQbit([_torrent("[FHD-1080] SSIS-123-C")])

        await _background_finalize_torrent(app, qbit, _add_context(), chat_id=1)

        self.assertEqual(qbit.created_categories, ["JAV"])
        self.assertEqual(qbit.set_categories, [("a" * 40, "JAV")])
        self.assertIn("a" * 40, app.bot_data["bot_state"].jav_processed_hashes)
        self.assertEqual(app.state_store.save_calls, 1)
        self.assertEqual(len(app.bot.messages), 1)
        self.assertIn("已识别 JAV", app.bot.messages[0]["text"])
        self.assertNotIn("reply_markup", app.bot.messages[0])

    async def test_jav_not_ready_message_uses_valid_html(self) -> None:
        app = FakeApplication()
        qbit = FakeQbit(
            [_torrent("[FHD-1080] SSIS-123-C")],
            files=[
                TorrentFile(
                    index=0,
                    name="SSIS-123.mp4",
                    size=2 * 1024 * 1024 * 1024,
                    priority=1,
                ),
                TorrentFile(index=1, name="cover.jpg", size=1024, priority=1),
            ],
            fail_small_file_priority=True,
        )

        await _background_finalize_torrent(app, qbit, _add_context(), chat_id=1)

        self.assertEqual(len(app.bot.messages), 1)
        self.assertIn("文件元数据暂未就绪", app.bot.messages[0]["text"])
        self.assertNotIn("后台处理失败", app.bot.messages[0]["text"])

    async def test_jav_torrent_checks_jellyfin_before_category(self) -> None:
        jellyfin = FakeJellyfin(enabled=True)
        app = FakeApplication(jellyfin=jellyfin)
        qbit = FakeQbit([_torrent("[FHD-1080] SSIS-123-C")])

        await _background_finalize_torrent(app, qbit, _add_context(), chat_id=1)

        self.assertEqual(jellyfin.queries, ["SSIS-123"])
        self.assertEqual(qbit.set_categories, [("a" * 40, "JAV")])

    async def test_existing_jellyfin_duplicate_is_deleted_before_category(self) -> None:
        jellyfin = FakeJellyfin([_jellyfin_item()], enabled=True)
        app = FakeApplication(
            jellyfin=jellyfin,
            jellyfin_duplicate_delete_enabled=True,
        )
        qbit = FakeQbit([_torrent("[FHD-1080] SSIS-123-C")])

        await _background_finalize_torrent(app, qbit, _add_context(), chat_id=1)

        self.assertEqual(jellyfin.queries, ["SSIS-123"])
        self.assertEqual(qbit.deleted_torrents, [("a" * 40, False)])
        self.assertEqual(qbit.set_categories, [])
        self.assertIn("Jellyfin 已存在同番号", app.bot.messages[0]["text"])

    async def test_4k_torrent_is_kept_when_jellyfin_duplicate_is_not_4k(self) -> None:
        jellyfin = FakeJellyfin([_jellyfin_item(path_suffix=".1080p")], enabled=True)
        app = FakeApplication(
            jellyfin=jellyfin,
            jellyfin_duplicate_delete_enabled=True,
        )
        qbit = FakeQbit(
            [_torrent("[FHD] SSIS-123")],
            files=[
                TorrentFile(
                    index=0,
                    name="SSIS-123.2160p.mkv",
                    size=2 * 1024 * 1024 * 1024,
                    priority=1,
                )
            ],
        )

        await _background_finalize_torrent(app, qbit, _add_context(), chat_id=1)

        self.assertEqual(jellyfin.queries, ["SSIS-123"])
        self.assertEqual(qbit.deleted_torrents, [])
        self.assertEqual(qbit.set_categories, [("a" * 40, "JAV")])
        self.assertIn("本次是 4K 版本", app.bot.messages[0]["text"])

    async def test_4k_torrent_is_deleted_when_jellyfin_duplicate_is_also_4k(self) -> None:
        jellyfin = FakeJellyfin([_jellyfin_item(path_suffix=".2160p")], enabled=True)
        app = FakeApplication(
            jellyfin=jellyfin,
            jellyfin_duplicate_delete_enabled=True,
        )
        qbit = FakeQbit(
            [_torrent("[FHD] SSIS-123")],
            files=[
                TorrentFile(
                    index=0,
                    name="SSIS-123.2160p.mkv",
                    size=2 * 1024 * 1024 * 1024,
                    priority=1,
                )
            ],
        )

        await _background_finalize_torrent(app, qbit, _add_context(), chat_id=1)

        self.assertEqual(jellyfin.queries, ["SSIS-123"])
        self.assertEqual(qbit.deleted_torrents, [("a" * 40, False)])
        self.assertEqual(qbit.set_categories, [])

    async def test_non_jav_torrent_gets_category_prompt(self) -> None:
        app = FakeApplication()
        qbit = FakeQbit([_torrent("ubuntu-24.04-live-server.iso")])

        await _background_finalize_torrent(app, qbit, _add_context(), chat_id=1)

        self.assertEqual(qbit.created_categories, [])
        self.assertEqual(qbit.set_categories, [])
        self.assertEqual(len(app.bot.messages), 1)
        self.assertIn("请选择移动到哪个分类", app.bot.messages[0]["text"])
        self.assertIn("reply_markup", app.bot.messages[0])
