import unittest

from app.callback_data import (
    CategoryCallbackPayload,
    TorrentCallback,
    build_category_callback,
    build_torrent_callback,
    parse_category_callback_payload,
    parse_torrent_callback,
)


class CallbackDataTests(unittest.TestCase):
    def test_torrent_callback_roundtrip(self) -> None:
        callback_data = build_torrent_callback("detail", "a" * 40, "active")

        self.assertEqual(callback_data, f"tor:detail:active:{'a' * 40}")
        self.assertEqual(
            parse_torrent_callback(callback_data),
            TorrentCallback(action="detail", view="active", payload="a" * 40),
        )

    def test_category_callback_roundtrip_stays_under_telegram_limit(self) -> None:
        torrent_hash = "a" * 40
        callback_data = build_category_callback(torrent_hash, 12)

        self.assertEqual(callback_data, f"tor:cat:all:{torrent_hash}:12")
        self.assertLessEqual(len(callback_data.encode("utf-8")), 64)
        callback = parse_torrent_callback(callback_data)
        self.assertIsNotNone(callback)
        self.assertEqual(
            parse_category_callback_payload(callback.payload),
            CategoryCallbackPayload(torrent_hash=torrent_hash, category_index=12),
        )

    def test_parse_torrent_callback_rejects_malformed_values(self) -> None:
        self.assertIsNone(parse_torrent_callback(""))
        self.assertIsNone(parse_torrent_callback("cat:detail:all:abc"))
        self.assertIsNone(parse_torrent_callback("tor:detail"))

    def test_parse_category_callback_payload_rejects_bad_index(self) -> None:
        self.assertIsNone(parse_category_callback_payload("a" * 40))
        self.assertIsNone(parse_category_callback_payload(f"{'a' * 40}:bad"))
