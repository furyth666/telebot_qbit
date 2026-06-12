import re
import unittest

from app.jav_rules import (
    DEFAULT_JAV_NAME_REGEX,
    _extract_jav_code,
    _extract_jav_lookup_code,
    _is_jav_title,
    _matches_add_context,
)
from app.add_links import AddContext
from app.qbit_client import TorrentSummary


class JavRulesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.pattern = re.compile(DEFAULT_JAV_NAME_REGEX)

    def test_extracts_standard_code_from_messy_title(self) -> None:
        self.assertEqual(
            _extract_jav_code("[FHD] ssis 123 sample title", self.pattern),
            "SSIS-123",
        )
        self.assertEqual(
            _extract_jav_code("PRWF_010-uncensored", self.pattern),
            "PRWF-010",
        )
        self.assertEqual(
            _extract_jav_code("abp.987.mp4", self.pattern),
            "ABP-987",
        )

    def test_extracts_vendor_specific_codes(self) -> None:
        cases = {
            "FC2 PPV 1234567": "FC2-PPV-1234567",
            "fc2-1234567": "FC2-PPV-1234567",
            "heyzo_hd_1234": "HEYZO-1234",
            "1pondo_010123_001": "1PONDO-010123-001",
            "caribbeancom 010123-001": "CARIB-010123-001",
            "Tokyo Hot n1234": "N-1234",
        }

        for title, expected in cases.items():
            with self.subTest(title=title):
                self.assertEqual(_extract_jav_code(title, self.pattern), expected)

    def test_ignores_noisy_prefixes_and_suffixes(self) -> None:
        cases = {
            "[FHD-1080] [hhd800.com] SSIS-123-C": "SSIS-123",
            "[4K][UHD-2160][domain.example] MIDV_777_ch.mp4": "MIDV-777",
            "h264-1080p FC2-PPVDB 1234567 leaked": "FC2-PPV-1234567",
            "ＳＳＩＳ－１２３　字幕": "SSIS-123",
        }

        for title, expected in cases.items():
            with self.subTest(title=title):
                self.assertEqual(_extract_jav_code(title, self.pattern), expected)

    def test_lookup_rejects_long_free_text_and_non_codes(self) -> None:
        self.assertIsNone(_extract_jav_lookup_code("hello world", self.pattern))
        self.assertIsNone(_extract_jav_lookup_code("x" * 81 + " ABP-123", self.pattern))

    def test_is_jav_title_uses_expanded_default_pattern(self) -> None:
        self.assertTrue(_is_jav_title("FC2-PPV-1234567", self.pattern))
        self.assertTrue(_is_jav_title("1pondo_010123_001", self.pattern))
        self.assertTrue(_is_jav_title("[FHD-1080] SSIS-123-C", self.pattern))
        self.assertFalse(_is_jav_title("ubuntu-24.04-live-server.iso", self.pattern))
        self.assertFalse(_is_jav_title("[FHD-1080] sample video.mp4", self.pattern))

    def test_http_download_name_hint_would_not_match_tracker_title(self) -> None:
        item = TorrentSummary(
            name="Slayed.24.10.29.Eve.Sweet.And.Lilly.Bell.XXX.2160p",
            hash="a" * 40,
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

        self.assertFalse(
            _matches_add_context(
                item,
                AddContext(
                    known_hashes=set(),
                    started_at=100,
                    name_hint="download.php",
                    is_magnet=False,
                ),
            )
        )
        self.assertTrue(
            _matches_add_context(
                item,
                AddContext(
                    known_hashes=set(),
                    started_at=100,
                    name_hint=None,
                    is_magnet=False,
                ),
            )
        )
