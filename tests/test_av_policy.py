import unittest
from unittest.mock import patch

from app.av_policy import (
    StashDuplicateStatus,
    extract_av_search_query,
    handle_stash_duplicate_policy,
)
from app.config import Settings
from app.llm_classifier import AvMetadata
from app.qbit_client import TorrentSummary
from app.runtime_state import runtime_context
from app.stash_client import StashScene
from app.state_store import BotState


def _torrent(name: str = "Test.2023.1080p.mp4", torrent_hash: str = "a" * 40) -> TorrentSummary:
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


class FakeStateStore:
    def __init__(self) -> None:
        self.save_calls = 0

    async def save_async(self, state: BotState) -> None:
        self.save_calls += 1


class FakeStash:
    def __init__(self, scenes: list[StashScene] | None = None, *, enabled: bool = True) -> None:
        self.scenes = scenes or []
        self._enabled = enabled
        self.queries: list[str] = []

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def find_scenes_by_query(self, query: str) -> list[StashScene]:
        self.queries.append(query)
        return self.scenes


class FakeApplication:
    def __init__(
        self,
        *,
        stash: FakeStash | None = None,
        llm_classify_enabled: bool = True,
    ) -> None:
        self.state_store = FakeStateStore()
        self.bot_data = {
            "settings": Settings(
                telegram_bot_token="token",
                telegram_allowed_user_ids=[1],
                qbit_base_url="http://qbit",
                qbit_username="user",
                qbit_password="pass",
                stash_base_url="http://stash.local:9999",
                llm_classify_enabled=llm_classify_enabled,
                llm_api_key="llm-key",
            ),
            "bot_state": BotState(),
            "state_store": self.state_store,
            "stash": stash or FakeStash(),
        }


class AvPolicyTests(unittest.IsolatedAsyncioTestCase):
    async def test_handle_stash_duplicate_policy_disabled_returns_none(self) -> None:
        app = FakeApplication(stash=FakeStash(enabled=False))

        result = await handle_stash_duplicate_policy(
            app, _torrent(), files=[]
        )

        self.assertEqual(result.status, StashDuplicateStatus.NONE)

    async def test_handle_stash_duplicate_policy_found_keep_when_same_or_lower_resolution(self) -> None:
        scene = StashScene(
            scene_id="scene-1",
            title="Test Scene 1080p",
            date="",
            studio="",
            performers=(),
            paths=(),
            tags=(),
        )
        app = FakeApplication(stash=FakeStash([scene]))

        with patch(
            "app.av_policy.extract_av_metadata",
            return_value=AvMetadata(
                title="Test Scene",
                performers=(),
                studio="",
                year="",
                search_query="Test Scene",
            ),
        ):
            result = await handle_stash_duplicate_policy(
                app, _torrent(name="Test Scene 720p.mp4"), files=[]
            )

        self.assertEqual(result.status, StashDuplicateStatus.FOUND_KEEP)
        self.assertEqual(result.query, "Test Scene")

    async def test_handle_stash_duplicate_policy_upgrade_keep_when_higher_resolution(self) -> None:
        scene = StashScene(
            scene_id="scene-1",
            title="Test Scene 720p",
            date="",
            studio="",
            performers=(),
            paths=(),
            tags=(),
        )
        app = FakeApplication(stash=FakeStash([scene]))

        with patch(
            "app.av_policy.extract_av_metadata",
            return_value=AvMetadata(
                title="Test Scene",
                performers=(),
                studio="",
                year="",
                search_query="Test Scene",
            ),
        ):
            result = await handle_stash_duplicate_policy(
                app, _torrent(name="Test Scene 1080p.mp4"), files=[]
            )

        self.assertEqual(result.status, StashDuplicateStatus.UPGRADE_KEEP)
        self.assertEqual(result.query, "Test Scene")

    async def test_handle_stash_duplicate_policy_returns_none_on_query_error(self) -> None:
        stash = FakeStash()

        async def fail_query(query: str) -> list[StashScene]:
            raise RuntimeError("stash unreachable")

        stash.find_scenes_by_query = fail_query
        app = FakeApplication(stash=stash)

        with patch(
            "app.av_policy.extract_av_metadata",
            return_value=AvMetadata(
                title="Test Scene",
                performers=(),
                studio="",
                year="",
                search_query="Test Scene",
            ),
        ):
            result = await handle_stash_duplicate_policy(
                app, _torrent(), files=[]
            )

        self.assertEqual(result.status, StashDuplicateStatus.NONE)

    async def test_extract_av_search_query_falls_back_to_torrent_name(self) -> None:
        app = FakeApplication(llm_classify_enabled=False)
        settings = runtime_context(app).settings

        query = await extract_av_search_query(
            settings,
            _torrent(name="[JAVDB] Some Studio - Scene Name"),
            files=[],
        )

        self.assertEqual(query, "Some Studio - Scene Name")


if __name__ == "__main__":
    unittest.main()
