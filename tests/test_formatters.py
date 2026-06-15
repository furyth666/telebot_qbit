import unittest

from app.config import Settings
from app.formatters import (
    build_list_keyboard,
    format_action_result,
    format_bytes,
    format_jellyfin_caption,
    format_large_file_threshold,
    format_speed,
    format_torrent_caption,
    format_torrent_overview,
    short_hash,
)
from app.jellyfin_client import JellyfinItem, JellyfinPerson
from app.qbit_client import TorrentSummary


def _torrent(name: str = "Name <Unsafe>") -> TorrentSummary:
    return TorrentSummary(
        name=name,
        hash="a" * 40,
        category="",
        state="downloading",
        progress=0.5,
        dlspeed=1024,
        upspeed=2048,
        eta=60,
        size=2 * 1024,
        completion_on=0,
        added_on=100,
    )


class FormatterTests(unittest.TestCase):
    def test_byte_speed_and_hash_helpers(self) -> None:
        self.assertEqual(format_bytes(1024), "1.0 KB")
        self.assertEqual(format_speed(2048), "2.0 KB/s")
        self.assertEqual(short_hash("abcdef123456"), "abcdef12")

    def test_large_file_threshold_formats_int_and_fractional_values(self) -> None:
        self.assertEqual(
            format_large_file_threshold(
                Settings(
                    telegram_bot_token="token",
                    telegram_allowed_user_ids=[1],
                    qbit_base_url="http://qbit",
                    qbit_username="user",
                    qbit_password="pass",
                    jav_large_file_threshold_gb=1,
                )
            ),
            "1 GB",
        )
        self.assertEqual(
            format_large_file_threshold(
                Settings(
                    telegram_bot_token="token",
                    telegram_allowed_user_ids=[1],
                    qbit_base_url="http://qbit",
                    qbit_username="user",
                    qbit_password="pass",
                    jav_large_file_threshold_gb=1.5,
                )
            ),
            "1.5 GB",
        )

    def test_torrent_text_escapes_user_controlled_values(self) -> None:
        caption = format_torrent_caption(_torrent(), 1)
        overview = format_torrent_overview("Recent <All>", [_torrent()])
        action = format_action_result("Deleted <Task>", "a<bad>")

        self.assertIn("Name &lt;Unsafe&gt;", caption)
        self.assertIn("Recent &lt;All&gt;", overview)
        self.assertIn("Deleted &lt;Task&gt;", action)
        self.assertIn("a&lt;bad&gt;", action)

    def test_list_keyboard_uses_detail_callback(self) -> None:
        keyboard = build_list_keyboard([_torrent()], filter_name="all")
        button = keyboard.inline_keyboard[0][0]

        self.assertEqual(button.text, "详情 aaaaaaaa")
        self.assertEqual(button.callback_data, "tor:detail:all:" + "a" * 40)

    def test_jellyfin_caption_includes_links_and_escapes_fields(self) -> None:
        item = JellyfinItem(
            item_id="item-1",
            server_id="server-1",
            name="SSIS-123 <Title>",
            path="/media/SSIS-123.mkv",
            overview="Overview <unsafe>",
            production_year=2024,
            premiere_date="2024-01-02T00:00:00.000Z",
            actors=(JellyfinPerson(person_id="actor-1", name="Actor <Name>"),),
        )

        caption = format_jellyfin_caption(
            "SSIS-123",
            item,
            2,
            public_base_url="http://jellyfin.local",
        )

        self.assertIn("SSIS-123 &lt;Title&gt;", caption)
        self.assertIn("Actor &lt;Name&gt;", caption)
        self.assertIn("Overview &lt;unsafe&gt;", caption)
        self.assertIn("http://jellyfin.local/web/index.html#!/details", caption)
        self.assertIn("共找到 2 条匹配", caption)
