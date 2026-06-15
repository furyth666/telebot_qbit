import unittest

from app.config import Settings
from app.jellyfin_client import JellyfinItem
from app.jav_policy import (
    JellyfinDuplicateStatus,
    JavFileSelectionResult,
    apply_jav_category_policy,
    apply_jav_file_selection,
    handle_jellyfin_duplicate_policy,
)
from app.qbit_client import TorrentFile, TorrentSummary
from app.runtime_state import runtime_context
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


def _jellyfin_item(
    name: str = "SSIS-123",
    *,
    path: str = "/media/SSIS-123.mp4",
) -> JellyfinItem:
    return JellyfinItem(
        item_id="item-1",
        server_id="server-1",
        name=name,
        path=path,
        overview="",
        production_year=None,
        premiere_date="",
        actors=(),
    )


class FakeStateStore:
    def __init__(self) -> None:
        self.save_calls = 0

    async def save_async(self, state: BotState) -> None:
        self.save_calls += 1


class FakeJellyfin:
    def __init__(self, items: list[JellyfinItem] | None = None, *, enabled: bool = True) -> None:
        self.items = items or []
        self._enabled = enabled
        self.queries: list[str] = []

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def find_by_code(self, code: str) -> list[JellyfinItem]:
        self.queries.append(code)
        return self.items


class FakeApplication:
    def __init__(
        self,
        *,
        jellyfin: FakeJellyfin | None = None,
        jellyfin_duplicate_delete_enabled: bool = False,
        jellyfin_duplicate_grace_hours: int = 3,
    ) -> None:
        self.state_store = FakeStateStore()
        self.bot_data = {
            "settings": Settings(
                telegram_bot_token="token",
                telegram_allowed_user_ids=[1],
                qbit_base_url="http://qbit",
                qbit_username="user",
                qbit_password="pass",
                jellyfin_duplicate_delete_enabled=jellyfin_duplicate_delete_enabled,
                jellyfin_duplicate_grace_hours=jellyfin_duplicate_grace_hours,
            ),
            "bot_state": BotState(),
            "state_store": self.state_store,
            "jellyfin": jellyfin or FakeJellyfin(),
        }


class FakeQbit:
    def __init__(
        self,
        *,
        files: list[TorrentFile],
        create_category_error: Exception | None = None,
        fail_small_file_priority: bool = False,
    ) -> None:
        self.files = files
        self.create_category_error = create_category_error
        self.fail_small_file_priority = fail_small_file_priority
        self.created_categories: list[str] = []
        self.set_categories: list[tuple[str, str]] = []
        self.deleted_torrents: list[tuple[str, bool]] = []
        self.file_priorities: list[tuple[str, list[int], int]] = []

    async def create_category(self, category: str) -> None:
        self.created_categories.append(category)
        if self.create_category_error:
            raise self.create_category_error

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
        self.file_priorities.append((torrent_hash, file_indexes, priority))


class JavPolicyTests(unittest.IsolatedAsyncioTestCase):
    async def test_apply_jav_file_selection_filters_small_files(self) -> None:
        app = FakeApplication()
        qbit = FakeQbit(
            files=[
                TorrentFile(index=0, name="movie.mp4", size=2 * 1024**3, priority=1),
                TorrentFile(index=1, name="sample.mp4", size=100 * 1024**2, priority=1),
            ],
        )

        result = await apply_jav_file_selection(app, qbit, "a" * 40)

        self.assertEqual(result, JavFileSelectionResult.FILTERED)
        self.assertEqual(
            qbit.file_priorities,
            [
                ("a" * 40, [0], 1),
                ("a" * 40, [1], 0),
            ],
        )

    async def test_apply_jav_file_selection_reports_not_ready_on_priority_failure(self) -> None:
        app = FakeApplication()
        qbit = FakeQbit(
            files=[
                TorrentFile(index=0, name="movie.mp4", size=2 * 1024**3, priority=1),
                TorrentFile(index=1, name="sample.mp4", size=100 * 1024**2, priority=1),
            ],
            fail_small_file_priority=True,
        )

        with self.assertLogs(level="ERROR") as logs:
            result = await apply_jav_file_selection(app, qbit, "a" * 40)

        self.assertEqual(result, JavFileSelectionResult.NOT_READY)
        self.assertIn("Failed to skip small files", "\n".join(logs.output))

    async def test_apply_jav_category_policy_marks_processed_even_when_category_exists(self) -> None:
        app = FakeApplication()
        qbit = FakeQbit(
            files=[
                TorrentFile(index=0, name="movie.mp4", size=2 * 1024**3, priority=1),
            ],
            create_category_error=RuntimeError("category exists"),
        )

        result = await apply_jav_category_policy(app, qbit, "a" * 40)

        self.assertEqual(result.category, "JAV")
        self.assertEqual(result.selection_result, JavFileSelectionResult.NO_FILTER_NEEDED)
        self.assertEqual(qbit.set_categories, [("a" * 40, "JAV")])
        self.assertIn("a" * 40, runtime_context(app).state.jav_processed_hashes)
        self.assertEqual(app.state_store.save_calls, 1)

    async def test_apply_jav_category_policy_raises_unexpected_create_error(self) -> None:
        app = FakeApplication()
        qbit = FakeQbit(
            files=[
                TorrentFile(index=0, name="movie.mp4", size=2 * 1024**3, priority=1),
            ],
            create_category_error=RuntimeError("network unavailable"),
        )

        with self.assertRaisesRegex(RuntimeError, "network unavailable"):
            await apply_jav_category_policy(app, qbit, "a" * 40)

        self.assertEqual(qbit.set_categories, [])
        self.assertNotIn("a" * 40, runtime_context(app).state.jav_processed_hashes)

    async def test_jellyfin_duplicate_policy_deletes_magnet_and_records_grace(self) -> None:
        app = FakeApplication(
            jellyfin=FakeJellyfin([_jellyfin_item()]),
            jellyfin_duplicate_delete_enabled=True,
        )
        qbit = FakeQbit(files=[])

        result = await handle_jellyfin_duplicate_policy(
            app,
            qbit,
            _torrent("SSIS-123"),
            is_magnet=True,
            code="SSIS-123",
        )

        state = runtime_context(app).state
        self.assertEqual(result.status, JellyfinDuplicateStatus.DELETED)
        self.assertEqual(result.first_item_path, "/media/SSIS-123.mp4")
        self.assertEqual(qbit.deleted_torrents, [("a" * 40, False)])
        self.assertIn("SSIS-123", state.jellyfin_duplicate_codes)
        self.assertIn("a" * 40, state.jav_processed_hashes)
        self.assertEqual(app.state_store.save_calls, 1)

    async def test_jellyfin_duplicate_policy_uses_grace_without_querying_jellyfin(self) -> None:
        jellyfin = FakeJellyfin([_jellyfin_item()])
        app = FakeApplication(jellyfin=jellyfin)
        runtime_context(app).state.jellyfin_duplicate_codes["SSIS-123"] = 9999999999
        qbit = FakeQbit(files=[])

        result = await handle_jellyfin_duplicate_policy(
            app,
            qbit,
            _torrent("SSIS-123"),
            is_magnet=True,
            code="SSIS-123",
        )

        self.assertEqual(result.status, JellyfinDuplicateStatus.WITHIN_GRACE)
        self.assertEqual(jellyfin.queries, [])
        self.assertEqual(qbit.deleted_torrents, [])

    async def test_jellyfin_duplicate_policy_keeps_4k_upgrade(self) -> None:
        app = FakeApplication(jellyfin=FakeJellyfin([_jellyfin_item()]))
        qbit = FakeQbit(files=[])

        result = await handle_jellyfin_duplicate_policy(
            app,
            qbit,
            _torrent("SSIS-123 2160p"),
            is_magnet=True,
            code="SSIS-123",
        )

        self.assertEqual(result.status, JellyfinDuplicateStatus.FOUR_K_EXCEPTION)
        self.assertEqual(qbit.deleted_torrents, [])

    async def test_jellyfin_duplicate_policy_keeps_when_delete_disabled(self) -> None:
        app = FakeApplication(jellyfin=FakeJellyfin([_jellyfin_item()]))
        qbit = FakeQbit(files=[])

        result = await handle_jellyfin_duplicate_policy(
            app,
            qbit,
            _torrent("SSIS-123"),
            is_magnet=True,
            code="SSIS-123",
        )

        self.assertEqual(result.status, JellyfinDuplicateStatus.FOUND_KEEP)
        self.assertEqual(qbit.deleted_torrents, [])
