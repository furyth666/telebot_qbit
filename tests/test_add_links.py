import unittest

from app.add_links import add_torrent_links
from app.config import Settings
from app.qbit_client import TorrentSummary


class FakeApplication:
    def __init__(self) -> None:
        self.bot_data = {
            "settings": Settings(
                telegram_bot_token="token",
                telegram_allowed_user_ids=[1],
                qbit_base_url="http://qbit",
                qbit_username="user",
                qbit_password="pass",
            )
        }


class FakeQbit:
    def __init__(self) -> None:
        self.added_urls: list[str] = []

    async def list_torrents(self, *, filter_name: str = "all") -> list:
        return []

    async def add_torrent_url_with_options(
        self,
        url: str,
        *,
        upload_limit: int | None = None,
        category: str | None = None,
    ) -> None:
        self.added_urls.append(url)


def _torrent(name: str, torrent_hash: str) -> TorrentSummary:
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


class FakeGrowingQbit:
    def __init__(self) -> None:
        self.torrents: list[TorrentSummary] = []
        self.added_urls: list[str] = []

    async def list_torrents(self, *, filter_name: str = "all") -> list[TorrentSummary]:
        return self.torrents

    async def add_torrent_url_with_options(
        self,
        url: str,
        *,
        upload_limit: int | None = None,
        category: str | None = None,
    ) -> None:
        self.added_urls.append(url)
        self.torrents.append(_torrent(f"download-{len(self.torrents)}", str(len(self.torrents)) * 40))


class AddLinksTests(unittest.IsolatedAsyncioTestCase):
    async def test_http_torrent_context_does_not_use_url_path_as_name_hint(self) -> None:
        app = FakeApplication()
        qbit = FakeQbit()

        result = await add_torrent_links(
            app,
            qbit,
            ["https://tracker.example/download.php?id=123&passkey=secret"],
        )

        self.assertEqual(result.success_count, 1)
        self.assertEqual(result.contexts[0].name_hint, None)
        self.assertFalse(result.contexts[0].is_magnet)

    async def test_magnet_context_keeps_dn_name_hint(self) -> None:
        app = FakeApplication()
        qbit = FakeQbit()

        result = await add_torrent_links(
            app,
            qbit,
            ["magnet:?xt=urn:btih:" + "a" * 40 + "&dn=The.Show.S01E01"],
        )

        self.assertEqual(result.success_count, 1)
        self.assertEqual(result.contexts[0].name_hint, "The.Show.S01E01")
        self.assertTrue(result.contexts[0].is_magnet)
        self.assertEqual(result.contexts[0].expected_hashes, {"a" * 40})

    async def test_http_add_refreshes_known_hash_cache_for_next_submission(self) -> None:
        app = FakeApplication()
        qbit = FakeGrowingQbit()

        first = await add_torrent_links(
            app,
            qbit,
            ["https://tracker.example/download.php?id=1"],
        )
        second = await add_torrent_links(
            app,
            qbit,
            ["https://tracker.example/download.php?id=2"],
        )

        self.assertEqual(first.contexts[0].known_hashes, set())
        self.assertEqual(second.contexts[0].known_hashes, {"0" * 40})
        self.assertEqual(first.contexts[0].expected_hashes, {"0" * 40})
        self.assertEqual(second.contexts[0].expected_hashes, {"1" * 40})
